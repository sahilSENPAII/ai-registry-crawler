import json
import uuid
from collections import Counter
from datetime import datetime, timezone

from app import store
from app.config import config
from app.crawler import hybrid
from app.crawler.classifier import classify
from app.crawler.collectors import collectors
from app.crawler.registry import build_record
from app.tracing import CRAWLER_AGENT, make_span

AGENT_NAME = CRAWLER_AGENT
COLLECTOR_HELP = {
    "collect_lambda": "Lambda functions, with their code, env vars, tags and IAM role",
    "collect_bedrock_agents": "Bedrock agents",
    "collect_sagemaker": "SageMaker endpoints and the models behind them",
    "collect_stepfunctions": "Step Functions state machines and their definitions",
    "collect_ecs": "ECS task definitions and their containers",
}
PLANNER_PROMPT = f"""You are aws-ai-crawler. You inventory the AI systems in one AWS account for an ISO/IEC 42001 \
AI registry. Call the collectors to list the account's resources. Each result shows what the deterministic rules \
concluded and marks with needs_review the resources they are unsure about. For each of those, call inspect_resource, \
then record_verdict. Steps are limited, so call several tools in one turn where you can. When every collector has \
been called and every needs_review resource has a verdict, reply with a one-line summary.

{hybrid.DEFINITIONS}"""


def _result(use, value, ok=True):
    content = [{"text": value}] if isinstance(value, str) else [{"json": value}]
    return {"toolResult": {"toolUseId": use["toolUseId"], "content": content, **({} if ok else {"status": "error"})}}


class CrawlerAgent:
    """collect -> classify -> register, tracing each step as OTel GenAI spans.
    With an LLM, a tool-use loop picks the collectors and judges what the rules are unsure about; the rules stay
    as guardrails: skipped collectors still run, and unconfirmed verdicts fall back to the rules."""

    def __init__(self, session, fetch_code, conn, llm=None):
        self.session, self.fetch_code, self.conn, self.llm = session, fetch_code, conn, llm

    def _span(self, name, start, attrs, status="ok", error_type=None):
        self.spans.append(make_span(AGENT_NAME, self.trace_id, self.root_id, "execute_tool", f"execute_tool {name}",
                                    start, {"gen_ai.tool.name": name, **attrs}, status, error_type))

    def run(self) -> dict:
        conn = self.conn
        run_id = store.start_run(conn, config.discovery_mode, [self.session.region_name])
        conn.commit()
        self.trace_id, self.root_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
        started = datetime.now(timezone.utc)
        self.collectors = collectors(self.fetch_code)
        self.spans, self.errors, self.collected, self.verdicts = [], [], {}, {}
        self.missing = "no LLM verdict for this resource"
        llm_spans = len(self.llm.spans) if self.llm else 0
        root = {}
        try:
            if self.llm:
                root |= self._plan()
            for collect in self.collectors:
                if collect.__name__ not in self.collected:
                    self._collect(collect, guardrail=self.llm is not None)
            counts, decided = self._register(run_id)
            # a failed collector hides its resources, so retiring now would retire systems that still exist
            retired = 0 if self.errors else store.retire_unseen(conn, run_id)
            store.finish_run(conn, run_id, counts, "; ".join(self.errors) or None)
            root |= {f"crawler.{k}": v for k, v in counts.items()} | {"crawler.retired": retired}
            root |= {f"crawler.decided_by.{k}": n for k, n in decided.items()}
            self._finish(run_id, started, root, llm_spans)
            conn.commit()
        except Exception as e:
            conn.rollback()
            store.finish_run(conn, run_id, error=f"{type(e).__name__}: {e}")
            self._finish(run_id, started, root, llm_spans, "error", type(e).__name__)
            conn.commit()
            raise
        return {"run_id": run_id, **counts, "retired": retired, "errors": self.errors, "decided_by": dict(decided),
                "llm": {k.removeprefix("crawler.llm."): v for k, v in root.items() if k.startswith("crawler.llm.")}}

    def _finish(self, run_id, started, root, llm_spans, status="ok", error_type=None):
        own = self.llm.spans[llm_spans:] if self.llm else []
        self.spans.insert(0, make_span(AGENT_NAME, self.trace_id, None, "invoke_agent", f"invoke_agent {AGENT_NAME}",
                                       started, root, status, error_type, span_id=self.root_id))
        store.insert_spans(self.conn, self.spans + own, run_id)

    def _plan(self) -> dict:
        llm = self.llm
        llm.trace = (self.trace_id, self.root_id)
        before = (llm.calls, llm.input_tokens, llm.output_tokens, llm.cost_usd)
        messages = [{"role": "user", "content": [{"text": "Crawl the account."}]}]
        budget_exceeded, error = False, None
        try:
            for _ in range(config.llm_max_steps):
                message = llm.converse(messages, PLANNER_PROMPT, self._tools(), purpose="plan")["output"]["message"]
                messages.append(message)
                uses = [block["toolUse"] for block in message["content"] if "toolUse" in block]
                if not uses:
                    break
                messages.append({"role": "user", "content": [self._run_tool(use) for use in uses]})
            else:
                budget_exceeded = True
                self.missing = f"no LLM verdict within LLM_MAX_STEPS ({config.llm_max_steps})"
        except Exception as e:  # the guardrails below still run every collector and decide every resource
            error = f"{type(e).__name__}: {e}"
            self.missing = f"the planner stopped: {error}"
        attrs = {"crawler.llm.model": llm.model_id, "crawler.llm.calls": llm.calls - before[0],
                 "crawler.llm.input_tokens": llm.input_tokens - before[1],
                 "crawler.llm.output_tokens": llm.output_tokens - before[2],
                 "crawler.llm.cost_usd": round(llm.cost_usd - before[3], 6),
                 "crawler.llm.max_steps": config.llm_max_steps, "crawler.llm.budget_exceeded": budget_exceeded}
        return attrs | ({"crawler.llm.error": error} if error else {})

    def _tools(self):
        no_input = {"json": {"type": "object", "properties": {}}}
        schema = hybrid.VERDICT_TOOL["inputSchema"]["json"]
        return [{"name": c.__name__, "description": f"List {COLLECTOR_HELP.get(c.__name__, c.__name__)}.",
                 "inputSchema": no_input} for c in self.collectors] + [
            {"name": "inspect_resource",
             "description": "Show a collected resource's env vars, tags, IAM actions, config and code "
                            "(secrets masked, code truncated).",
             "inputSchema": {"json": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
            {**hybrid.VERDICT_TOOL, "inputSchema": {"json": {**schema, "required": ["name", *schema["required"]]}}},
        ]

    def _run_tool(self, use) -> dict:
        name, args = use["name"], use.get("input") or {}
        collect = next((c for c in self.collectors if c.__name__ == name), None)
        if collect:
            result = self._collect(collect)
            return _result(use, result, "error" not in result)
        if name == "inspect_resource":
            start, found = datetime.now(timezone.utc), self._find(args.get("name"))
            attrs = {"gen_ai.tool.call.arguments": json.dumps(args), "crawler.resource": args.get("name")}
            if not found:
                self._span(name, start, attrs, "error", "UnknownResource")
                return _result(use, {"error": f"no collected resource named {args.get('name')!r}"}, False)
            self._span(name, start, attrs)
            return _result(use, hybrid.context(found[0]))
        if name == "record_verdict":
            result = self._record(args)
            return _result(use, result, result["accepted"])
        return _result(use, {"error": f"unknown tool {name}"}, False)

    def _find(self, name):
        # shortcut: resources are looked up by name only; upgrade to service/name if names collide across services
        return next(((r, c) for found in self.collected.values() for r, c in found if r.name == name), None)

    def _collect(self, collect, guardrail=False) -> dict:
        name = collect.__name__
        if name in self.collected:
            return self._summary(self.collected[name])
        start = datetime.now(timezone.utc)
        attrs = {"crawler.guardrail": "not called by the planner, run anyway"} if guardrail else {}
        try:
            resources = collect(self.session)
        except Exception as e:
            self.errors.append(f"{name}: {e}")
            self.collected[name] = []
            self._span(name, start, attrs, "error", type(e).__name__)
            return {"error": str(e)}
        self.collected[name] = [(r, classify(r)) for r in resources]
        self._span(name, start, attrs | {"crawler.resources": len(resources)})
        return self._summary(self.collected[name])

    def _summary(self, found):
        return {"resources": [{
            "name": r.name, "service": r.service, "needs_review": not hybrid.confident(r, c),
            "rules": {"is_ai": c.is_ai, "is_agent": c.is_agent, "agent_score": c.agent_score,
                      "signals": sorted({e.signal for e in c.evidence})},
        } for r, c in found]}

    def _record(self, args) -> dict:
        start, name = datetime.now(timezone.utc), args.get("name")
        found = self._find(name)
        problems = hybrid.validate(args, found[0]) if found else [f"no collected resource named {name!r}"]
        if found:
            self.verdicts[name] = args
        attrs = {"gen_ai.tool.call.arguments": json.dumps(args), "crawler.resource": name}
        self._span("record_verdict", start, attrs | ({"crawler.verdict.rejected": problems} if problems else {}))
        if problems:
            return {"accepted": False, "problems": problems}
        if hybrid.confident(*found):
            return {"accepted": True, "note": "the rules are confident about this resource, so they decide it"}
        return {"accepted": True}

    def _register(self, run_id):
        counts = {"resources_scanned": 0, "ai_systems_found": 0, "agents_found": 0}
        decided = Counter()
        for collect in self.collectors:
            for resource, rules in self.collected.get(collect.__name__, []):
                if self.llm is None or hybrid.confident(resource, rules):
                    c, by, verdict = rules, "rules", None
                else:
                    c, by, verdict = hybrid.decide(resource, rules, self.verdicts.get(resource.name), self.missing)
                decided[by] += 1
                counts["resources_scanned"] += 1
                if c.is_ai:
                    store.upsert_registry(self.conn, build_record(resource, c, run_id, by, verdict))
                    counts["ai_systems_found"] += 1
                    counts["agents_found"] += c.is_agent
        return counts, decided
