"""LLM clients for the crawler: Bedrock Converse (the default, so code and config stay in AWS), the Gemini API, Groq,
and a scripted fake for tests. All speak Converse-shaped messages, and every call is traced as a chat span."""
import json
import re
import time
import uuid
from datetime import datetime, timezone

import httpx

from app.config import config
from app.pricing import cost_usd
from app.tracing import CRAWLER_AGENT, make_span


class LLM:
    provider = None

    def __init__(self, model_id):
        self.model_id = model_id
        self.spans = []
        self.trace = (None, None)  # (trace_id, parent span id) the next chat spans belong to
        self.calls = self.input_tokens = self.output_tokens = 0
        self.cost_usd = 0.0

    def converse(self, messages, system, tools, purpose) -> dict:
        start = datetime.now(timezone.utc)
        attrs = {"gen_ai.request.model": self.model_id, "crawler.llm.purpose": purpose}
        try:
            response = self._converse(messages, system, tools)
        except Exception as e:
            self.spans.append(make_span(CRAWLER_AGENT, *self.trace, "chat", f"chat {self.model_id}", start, attrs,
                                        "error", type(e).__name__))
            raise
        usage = response.get("usage", {})
        in_tokens, out_tokens = usage.get("inputTokens", 0), usage.get("outputTokens", 0)
        cost = cost_usd(self.model_id, in_tokens, out_tokens) or 0.0
        self.calls += 1
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        self.cost_usd += cost
        span = make_span(CRAWLER_AGENT, *self.trace, "chat", f"chat {self.model_id}", start, {
            **attrs, "gen_ai.usage.input_tokens": in_tokens, "gen_ai.usage.output_tokens": out_tokens,
            "gen_ai.response.finish_reasons": [response.get("stopReason")],
        })
        span["cost_usd"] = cost
        self.spans.append(span)
        return response

    def _converse(self, messages, system, tools):
        raise NotImplementedError


class BedrockLLM(LLM):
    provider = "bedrock"

    def __init__(self, session, model_id=None):
        super().__init__(model_id or config.llm_model_id)
        self.client = session.client("bedrock-runtime")

    def _converse(self, messages, system, tools):
        # no temperature: current Claude models reject sampling parameters
        return self.client.converse(modelId=self.model_id, system=[{"text": system}], messages=messages,
                                    toolConfig={"tools": [{"toolSpec": t} for t in tools]},
                                    inferenceConfig={"maxTokens": 4096})


class LLMError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class HttpLLM(LLM):
    """An LLM behind an HTTP API, keyed from backend/.env. Rate limits are waited out, as the server asks, and dropped
    connections retried."""
    RETRIES, MAX_WAIT_SECONDS = 6, 90  # a longer wait means a daily quota, which retrying won't fix

    def __init__(self, model_id, client, base_url, key, headers):
        super().__init__(model_id or config.llm_model_id)
        if client is None and not key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set; put it in backend/.env")
        self.client = client or httpx.Client(base_url=base_url, timeout=120, headers=headers)

    def _post(self, path, body):
        for attempt in range(self.RETRIES):
            try:
                r = self.client.post(path, json=body)
            except httpx.TransportError:
                if attempt == self.RETRIES - 1:
                    raise
                time.sleep(5 * 2 ** attempt)
                continue
            if r.status_code not in (429, 503) or attempt == self.RETRIES - 1:
                break
            wait = _retry_delay(r) or 5 * 2 ** attempt
            if wait > self.MAX_WAIT_SECONDS:
                break
            time.sleep(wait)
        if r.is_error:
            error = r.json().get("error", {}) if "json" in r.headers.get("content-type", "") else {}
            message = " ".join(error.get("message", r.text).split())[:500]
            code = error.get("status") or error.get("code") or ""
            raise LLMError(f"{self.provider.capitalize()} {r.status_code} {code}: {message}", code)
        return r.json()


class GeminiLLM(HttpLLM):
    """The Gemini API's generateContent, translated to and from Converse shapes."""
    provider = "gemini"

    def __init__(self, model_id=None, client=None):
        super().__init__(model_id, client, config.gemini_base_url, config.gemini_api_key,
                         {"x-goog-api-key": config.gemini_api_key})

    def _converse(self, messages, system, tools):
        names = {b["toolUse"]["toolUseId"]: b["toolUse"]["name"] for m in messages for b in m["content"] if "toolUse" in b}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            # assistant turns are sent back as Gemini returned them, keeping its thought signatures
            "contents": [m.get("gemini") or {"role": "model" if m["role"] == "assistant" else "user",
                                             "parts": [_gemini_part(b, names) for b in m["content"]]} for m in messages],
            "generationConfig": {"maxOutputTokens": 4096},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t["description"],
                                                        "parametersJsonSchema": t["inputSchema"]["json"]} for t in tools]}]
        data = self._post(f"/models/{self.model_id}:generateContent", body)
        candidate = (data.get("candidates") or [{}])[0]
        content = candidate.get("content") or {"role": "model", "parts": []}
        blocks = []
        for part in content.get("parts", []):
            if "functionCall" in part:
                call = part["functionCall"]
                blocks.append({"toolUse": {"toolUseId": uuid.uuid4().hex, "name": call["name"],
                                           "input": call.get("args", {})}})
            elif "text" in part and not part.get("thought"):
                blocks.append({"text": part["text"]})
        usage = data.get("usageMetadata", {})
        stop = "tool_use" if any("toolUse" in b for b in blocks) else \
            "max_tokens" if candidate.get("finishReason") == "MAX_TOKENS" else "end_turn"
        return {"output": {"message": {"role": "assistant", "content": blocks, "gemini": content}}, "stopReason": stop,
                "usage": {"inputTokens": usage.get("promptTokenCount", 0),
                          "outputTokens": usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)}}


class GroqLLM(HttpLLM):
    """Groq's OpenAI-compatible chat completions, translated to and from Converse shapes."""
    provider = "groq"

    def __init__(self, model_id=None, client=None):
        super().__init__(model_id, client, config.groq_base_url, config.groq_api_key,
                         {"Authorization": f"Bearer {config.groq_api_key}"})

    def _converse(self, messages, system, tools):
        body = {"model": self.model_id, "messages": _openai_messages(system, messages), "max_completion_tokens": 4096}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["inputSchema"]["json"]}} for t in tools]
        try:
            data = self._post("/chat/completions", body)
        except LLMError as e:
            if e.code != "tool_use_failed":
                raise
            # Groq rejects a malformed tool call; treat it like a model that answered in prose
            return {"output": {"message": {"role": "assistant", "content": [{"text": str(e)}]}},
                    "stopReason": "end_turn", "usage": {}}
        choice = data["choices"][0]
        message = choice["message"]
        calls = message.get("tool_calls") or []
        blocks = [{"text": message["content"]}] if message.get("content") else []
        blocks += [{"toolUse": {"toolUseId": c["id"], "name": c["function"]["name"],
                                "input": json.loads(c["function"]["arguments"] or "{}")}} for c in calls]
        stop = "tool_use" if calls else "max_tokens" if choice.get("finish_reason") == "length" else "end_turn"
        usage = data.get("usage", {})
        return {"output": {"message": {"role": "assistant", "content": blocks}}, "stopReason": stop,
                "usage": {"inputTokens": usage.get("prompt_tokens", 0), "outputTokens": usage.get("completion_tokens", 0)}}


def _tool_output(result):
    value = result["content"][0].get("json", result["content"][0].get("text"))
    return {"error" if result.get("status") == "error" else "result": value}


def _gemini_part(block, names):
    if "toolResult" not in block:
        return {"text": block["text"]}
    result = block["toolResult"]
    return {"functionResponse": {"name": names[result["toolUseId"]], "response": _tool_output(result)}}


def _openai_messages(system, messages):
    chat = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "assistant":
            calls = [{"id": b["toolUse"]["toolUseId"], "type": "function",
                      "function": {"name": b["toolUse"]["name"], "arguments": json.dumps(b["toolUse"]["input"])}}
                     for b in m["content"] if "toolUse" in b]
            chat.append({"role": "assistant", "content": "".join(b.get("text", "") for b in m["content"]) or None,
                         **({"tool_calls": calls} if calls else {})})
            continue
        for b in m["content"]:
            if "toolResult" in b:
                chat.append({"role": "tool", "tool_call_id": b["toolResult"]["toolUseId"],
                             "content": json.dumps(_tool_output(b["toolResult"]))})
            else:
                chat.append({"role": "user", "content": b["text"]})
    return chat


def _retry_delay(response):
    """Seconds to wait: Groq's retry-after header, or the RetryInfo in a Gemini error, e.g. "31s"."""
    try:
        if "retry-after" in response.headers:
            return float(response.headers["retry-after"])
        details = response.json()["error"]["details"]
        delay = next(d["retryDelay"] for d in details if "retryDelay" in d)
        return float(re.fullmatch(r"([\d.]+)s", delay).group(1))
    except (ValueError, KeyError, StopIteration, AttributeError):
        return None


def make_llm(session, model_id=None) -> LLM:
    if config.llm_provider == "bedrock":
        return BedrockLLM(session, model_id)
    llm = {"gemini": GeminiLLM, "groq": GroqLLM}.get(config.llm_provider)
    if llm is None:
        raise ValueError(f"LLM_PROVIDER must be bedrock, gemini or groq, not {config.llm_provider!r}")
    return llm(model_id)


class FakeLLM(LLM):
    """script: a list of responses returned in order, or a function (messages, system, tools) -> response."""

    def __init__(self, script, model_id=None):
        super().__init__(model_id or config.llm_model_id)
        self.script = script
        self.requests = []

    def _converse(self, messages, system, tools):
        self.requests.append(list(messages))
        if callable(self.script):
            return self.script(messages, system, tools)
        if not self.script:
            raise RuntimeError("FakeLLM script is exhausted")
        return self.script.pop(0)


def tool_use(*calls, tokens=(1200, 150)):
    """A scripted response asking for tools: tool_use(("collect_lambda", {}), ("inspect_resource", {"name": "x"}))."""
    content = [{"toolUse": {"toolUseId": f"tool-{i}", "name": name, "input": args}} for i, (name, args) in enumerate(calls)]
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": "tool_use",
            "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1]}}


def reply(text, tokens=(1200, 40)):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn",
            "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1]}}
