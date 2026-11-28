import dataclasses
import json

import httpx
import pytest

from app import llm as llm_module
from app.config import config
from app.crawler.hybrid import VERDICT_TOOL
from app.llm import FakeLLM, GeminiLLM, GroqLLM, make_llm, reply, tool_use
from app.pricing import cost_usd
from app.recommend.catalog import MODELS


def test_calls_are_traced_as_chat_spans_with_cost():
    llm = FakeLLM([tool_use(("collect_lambda", {}), tokens=(10_000, 2_000)), reply("done", tokens=(500, 20))],
                  model_id="au.anthropic.claude-haiku-5-5")
    llm.trace = ("trace-1", "root-1")
    llm.converse([{"role": "user", "content": [{"text": "go"}]}], "system", [], purpose="plan")
    llm.converse([], "system", [], purpose="plan")
    first = llm.spans[0]
    assert (first["trace_id"], first["parent_span_id"]) == ("trace-1", "root-1")
    assert first["attributes"]["gen_ai.agent.name"] == "aws-ai-crawler"
    assert first["attributes"]["gen_ai.operation.name"] == "chat"
    assert first["attributes"]["gen_ai.usage.input_tokens"] == 10_000
    assert first["cost_usd"] == pytest.approx((10_000 * 0.10 + 2_000 * 0.50) / 1_000_000)
    assert (llm.calls, llm.input_tokens, llm.output_tokens) == (2, 10_500, 2_020)
    assert llm.cost_usd == pytest.approx(sum(s["cost_usd"] for s in llm.spans))


def test_failed_call_is_traced_as_an_error():
    def throttled(messages, system, tools):
        raise RuntimeError("ThrottlingException")

    llm = FakeLLM(throttled)
    with pytest.raises(RuntimeError):
        llm.converse([], "system", [], purpose="plan")
    assert llm.spans[0]["status"] == "error" and llm.spans[0]["attributes"]["error.type"] == "RuntimeError"
    assert llm.calls == 0


def test_crawler_model_is_priced_but_never_recommended():
    for profile in ["au.anthropic.claude-haiku-5-5", "global.anthropic.claude-haiku-5-5", "anthropic.claude-haiku-5-5"]:
        assert cost_usd(profile, 1_000_000, 0) == pytest.approx(0.10)
    assert cost_usd("au.anthropic.claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert cost_usd("gemini-3.1-pro-preview", 1_000_000, 1_000_000) == pytest.approx(14.0)
    assert cost_usd("openai/gpt-oss-120b", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert not any("haiku-5-5" in m or "sonnet-5-5" in m or m.startswith(("gemini-3", "openai/")) for m in MODELS)


def http_llm(cls, model, *responses):
    """An HTTP LLM whose calls return these (status, body[, headers]) in order; sent bodies land in .sent."""
    sent, queue = [], list(responses)

    def handler(request):
        sent.append(json.loads(request.content))
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        status, body, *headers = response
        return httpx.Response(status, json=body, headers=headers[0] if headers else None)

    llm = cls(model, httpx.Client(base_url="https://llm.test", transport=httpx.MockTransport(handler)))
    llm.sent = sent
    return llm


def gemini(*responses):
    return http_llm(GeminiLLM, "gemini-3-flash-preview", *responses)


def groq(*responses):
    return http_llm(GroqLLM, "openai/gpt-oss-20b", *responses)


def candidate(*parts, usage=(1000, 50, 200)):
    return 200, {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": "STOP"}],
                 "usageMetadata": {"promptTokenCount": usage[0], "candidatesTokenCount": usage[1], "thoughtsTokenCount": usage[2]}}


def test_gemini_speaks_converse_shapes():
    call = {"functionCall": {"name": "record_verdict", "args": {"is_ai": True}}, "thoughtSignature": "sig-1"}
    llm = gemini(candidate(call), candidate({"text": "Done."}))
    first = llm.converse([{"role": "user", "content": [{"text": "Classify x."}]}], "system", [VERDICT_TOOL], purpose="plan")
    message = first["output"]["message"]
    use = message["content"][0]["toolUse"]
    assert (first["stopReason"], use["name"], use["input"]) == ("tool_use", "record_verdict", {"is_ai": True})
    assert first["usage"] == {"inputTokens": 1000, "outputTokens": 250}  # thinking is billed as output
    assert llm.spans[0]["cost_usd"] == pytest.approx((1000 * 0.50 + 250 * 3.00) / 1_000_000)

    result = {"toolResult": {"toolUseId": use["toolUseId"], "content": [{"json": {"accepted": True}}]}}
    second = llm.converse([{"role": "user", "content": [{"text": "Classify x."}]}, message,
                           {"role": "user", "content": [result]}], "system", [VERDICT_TOOL], purpose="plan")
    assert second["stopReason"] == "end_turn" and second["output"]["message"]["content"] == [{"text": "Done."}]

    body = llm.sent[1]
    assert body["systemInstruction"] == {"parts": [{"text": "system"}]}
    assert body["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == VERDICT_TOOL["inputSchema"]["json"]
    assert body["contents"][1] == {"role": "model", "parts": [call]}  # sent back with its thought signature
    assert body["contents"][2]["parts"] == [{"functionResponse": {"name": "record_verdict",
                                                                  "response": {"result": {"accepted": True}}}}]


def test_gemini_waits_out_rate_limits(monkeypatch):
    waits = []
    monkeypatch.setattr(llm_module.time, "sleep", waits.append)
    limited = 429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "per minute",
                              "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}
    llm = gemini(limited, (503, {"error": {"status": "UNAVAILABLE"}}), candidate({"text": "ok"}))
    assert llm.converse([], "system", [], purpose="plan")["output"]["message"]["content"] == [{"text": "ok"}]
    assert waits == [7.0, 10] and llm.calls == 1


def test_gemini_gives_up_on_a_daily_quota(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: pytest.fail("should not wait for a daily quota"))
    daily = 429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "requests per day limit: 0",
                            "details": [{"retryDelay": "78325s"}]}}
    with pytest.raises(RuntimeError, match="Gemini 429 RESOURCE_EXHAUSTED: requests per day limit: 0"):
        gemini(daily).converse([], "system", [], purpose="plan")


def completion(message, finish="stop", usage=(1000, 100)):
    return 200, {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


def test_groq_speaks_converse_shapes():
    call = {"id": "call_1", "type": "function", "function": {"name": "inspect_resource", "arguments": '{"name": "x"}'}}
    llm = groq(completion({"content": None, "tool_calls": [call]}, "tool_calls"), completion({"content": "Done."}))
    first = llm.converse([{"role": "user", "content": [{"text": "Crawl."}]}], "system", [VERDICT_TOOL], purpose="plan")
    message = first["output"]["message"]
    assert first["stopReason"] == "tool_use" and first["usage"] == {"inputTokens": 1000, "outputTokens": 100}
    assert message["content"] == [{"toolUse": {"toolUseId": "call_1", "name": "inspect_resource", "input": {"name": "x"}}}]
    assert llm.spans[0]["cost_usd"] == pytest.approx((1000 * 0.075 + 100 * 0.30) / 1_000_000)

    result = {"toolResult": {"toolUseId": "call_1", "content": [{"text": "not found"}], "status": "error"}}
    second = llm.converse([{"role": "user", "content": [{"text": "Crawl."}]}, message,
                           {"role": "user", "content": [result]}], "system", [VERDICT_TOOL], purpose="plan")
    assert second["stopReason"] == "end_turn" and second["output"]["message"]["content"] == [{"text": "Done."}]
    body = llm.sent[1]
    assert body["model"] == "openai/gpt-oss-20b" and body["tools"][0]["function"]["name"] == "record_verdict"
    assert body["messages"] == [
        {"role": "system", "content": "system"}, {"role": "user", "content": "Crawl."},
        {"role": "assistant", "content": None, "tool_calls": [{**call, "function": {**call["function"], "arguments": '{"name": "x"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"error": "not found"}'},
    ]


def test_groq_retries_drops_and_rate_limits_and_survives_a_malformed_tool_call(monkeypatch):
    waits = []
    monkeypatch.setattr(llm_module.time, "sleep", waits.append)
    limited = 429, {"error": {"message": "tokens per minute", "code": "rate_limit_exceeded"}}, {"retry-after": "3"}
    malformed = 400, {"error": {"message": "Failed to call a function.", "code": "tool_use_failed"}}
    llm = groq(httpx.RemoteProtocolError("Server disconnected"), limited, malformed)
    response = llm.converse([], "system", [VERDICT_TOOL], purpose="plan")
    assert waits == [5, 3.0] and response["stopReason"] == "end_turn"
    assert response["output"]["message"]["content"] == [{"text": "Groq 400 tool_use_failed: Failed to call a function."}]


def test_provider_is_chosen_by_config(monkeypatch):
    monkeypatch.setattr(llm_module, "config", dataclasses.replace(config, llm_provider="gemini", gemini_api_key=""))
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        make_llm(None)
    monkeypatch.setattr(llm_module, "config", dataclasses.replace(config, llm_provider="groq", groq_api_key=""))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
        make_llm(None)
    monkeypatch.setattr(llm_module, "config", dataclasses.replace(config, llm_provider="openai"))
    with pytest.raises(ValueError):
        make_llm(None)
    assert "gemini_api_key" not in repr(config) and "groq_api_key" not in repr(config)
