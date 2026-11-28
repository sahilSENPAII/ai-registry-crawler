"""Reads agent span logs from CloudWatch, stores them and runs behaviour rules over them."""
import json
import re
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import groupby

from app import store
from app.config import config
from app.crawler.classifier import WRITE_VERBS
from app.pricing import cost_usd
from app.tracing import CRAWLER_AGENT, make_span

AGENT_NAME = "ai-observability-agent"
THRESHOLDS = {"tool_loop_calls": 4, "token_spike_ratio": 5, "token_spike_min_tokens": 20_000, "slow_run_ratio": 3}
SEVERITY = {
    "unregistered_agent": "high",
    "undeclared_tool": "high",
    "side_effect_without_approval": "critical",
    "tool_loop": "medium",
    "token_spike": "medium",
    "run_error": "medium",
    "approval_denied": "low",
    "collector_failed": "medium",
    "llm_verdict_rejected": "medium",
    "slow_run": "low",
    "llm_budget_exceeded": "low",
}
SENSITIVE_KEY = re.compile(r"email|phone|ssn|card|password|address", re.I)


def _redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if SENSITIVE_KEY.search(k) else _redact(v) for k, v in value.items()}
    return value


def normalise(event) -> dict:
    span = json.loads(event["message"])
    attrs = _redact(span.get("attributes", {}))
    args = attrs.get("gen_ai.tool.call.arguments")
    if isinstance(args, str):
        try:
            attrs["gen_ai.tool.call.arguments"] = json.dumps(_redact(json.loads(args)))
        except json.JSONDecodeError:
            pass
    span["attributes"] = attrs
    start = datetime.fromisoformat(span["start_time"])
    end = datetime.fromisoformat(span["end_time"]) if span.get("end_time") else None
    span["duration_ms"] = int((end - start).total_seconds() * 1000) if end else None
    span["cost_usd"] = cost_usd(attrs.get("gen_ai.request.model"), attrs.get("gen_ai.usage.input_tokens"),
                                attrs.get("gen_ai.usage.output_tokens"))
    return span


def _money(amount):
    return f"${amount:,.0f}"


def _args(span):
    try:
        return json.loads(span["attributes"].get("gen_ai.tool.call.arguments") or "{}")
    except json.JSONDecodeError:
        return {}


def _op(span):
    return span["attributes"].get("gen_ai.operation.name")


def _finding(rule, agent, row, description, span=None, **details):
    return {
        "rule": rule, "severity": SEVERITY[rule], "agent_name": agent, "registry_id": row["id"] if row else None,
        "trace_id": span["trace_id"] if span else None, "span_id": span["span_id"] if span else None,
        "description": description, "details": details,
    }


def detect_anomalies(spans, registry) -> list[dict]:
    """registry: agent_name -> {"id", "tools": [{"name", ...}], "approval_threshold"} for registered agents."""
    by_agent = defaultdict(list)
    for span in spans:
        by_agent[span["attributes"]["gen_ai.agent.name"]].append(span)

    findings = []
    for agent, agent_spans in sorted(by_agent.items()):
        if any(s["attributes"].get("internal") for s in agent_spans):
            continue
        row = registry.get(agent)
        traces = defaultdict(list)
        for span in sorted(agent_spans, key=lambda s: s["start_time"]):
            traces[span["trace_id"]].append(span)

        if row is None:
            findings.append(_finding("unregistered_agent", agent, None,
                                     f"{agent} logged {len(agent_spans)} spans in {len(traces)} runs but is not in the AI registry",
                                     spans=len(agent_spans), traces=len(traces)))
        declared = {t["name"] for t in row["tools"]} if row else None
        threshold = row.get("approval_threshold") if row else None
        undeclared = defaultdict(list)
        totals = {}

        for trace_id, trace in traces.items():
            tool_spans = [s for s in trace if _op(s) == "execute_tool"]
            for span in tool_spans:
                tool = span["attributes"]["gen_ai.tool.name"]
                if declared is not None and tool not in declared:
                    undeclared[tool].append(span)
                amount = _args(span).get("amount")
                if threshold is not None and tool.split("_")[0] in WRITE_VERBS and isinstance(amount, (int, float)) \
                        and amount > threshold:
                    approved = any(_op(s) == "human_approval" and s["start_time"] <= span["start_time"]
                                   and s["attributes"].get("approval.decision") == "approved"
                                   and s["attributes"].get("approval.action") in (None, tool) for s in trace)
                    if not approved:
                        findings.append(_finding(
                            "side_effect_without_approval", agent, row,
                            f"{tool} of {_money(amount)} without approval (threshold {_money(threshold)})",
                            span, tool=tool, amount=amount, threshold=threshold))

            for tool, run in groupby(tool_spans, key=lambda s: s["attributes"]["gen_ai.tool.name"]):
                run = list(run)
                if len(run) >= THRESHOLDS["tool_loop_calls"]:
                    findings.append(_finding("tool_loop", agent, row, f"{tool} called {len(run)} times in a row in one run",
                                             run[0], tool=tool, calls=len(run)))

            errors = [s for s in trace if s.get("status") == "error"]
            if errors:
                span = next((s for s in errors if s.get("parent_span_id")), errors[0])
                error_type = span["attributes"].get("error.type", "unknown error")
                findings.append(_finding("run_error", agent, row, f"run failed with {error_type} in {span.get('name')}",
                                         span, error_type=error_type))

            for span in trace:
                if _op(span) == "human_approval" and span["attributes"].get("approval.decision") == "denied":
                    a = span["attributes"]
                    amount = a.get("approval.amount")
                    of = f" of {_money(amount)}" if isinstance(amount, (int, float)) else ""
                    findings.append(_finding("approval_denied", agent, row,
                                             f"approver denied {a.get('approval.action')}{of}; the agent stopped",
                                             span, action=a.get("approval.action"), amount=amount))

            totals[trace_id] = sum((s["attributes"].get("gen_ai.usage.input_tokens") or 0)
                                   + (s["attributes"].get("gen_ai.usage.output_tokens") or 0) for s in trace)

        for tool, tool_spans in undeclared.items():
            findings.append(_finding("undeclared_tool", agent, row,
                                     f"{agent} called {tool} ({len(tool_spans)}x), which is not in its declared tools",
                                     tool_spans[0], tool=tool, calls=len(tool_spans), declared=sorted(declared)))

        if totals:
            median = statistics.median(totals.values())
            for trace_id, total in totals.items():
                if total > THRESHOLDS["token_spike_min_tokens"] and total > THRESHOLDS["token_spike_ratio"] * median:
                    root = traces[trace_id][0]
                    findings.append(_finding("token_spike", agent, row,
                                             f"run used {total:,} tokens, {total / median:.0f}x the agent's median of {median:,.0f}",
                                             root, tokens=total, median=median))
    return findings


def detect_crawler_anomalies(spans) -> list[dict]:
    """spans: the crawler's own agent_traces rows over the lookback window."""
    agent, findings = CRAWLER_AGENT, []
    durations = [s["duration_ms"] for s in spans if s["operation"] == "invoke_agent" and s["duration_ms"] is not None]
    median = statistics.median(durations) if durations else None
    for s in spans:
        a = s["attributes"]
        if s["operation"] == "execute_tool" and (s["tool_name"] or "").startswith("collect_") and s["status"] == "error":
            findings.append(_finding("collector_failed", agent, None, f"{s['tool_name']} failed with {s['error_type']}",
                                     s, tool=s["tool_name"], error_type=s["error_type"]))
        if a.get("crawler.verdict.rejected"):
            findings.append(_finding("llm_verdict_rejected", agent, None,
                                     f"LLM verdict on {a.get('crawler.resource')} rejected: {'; '.join(a['crawler.verdict.rejected'])}",
                                     s, resource=a.get("crawler.resource"), problems=a["crawler.verdict.rejected"]))
        if s["operation"] != "invoke_agent":
            continue
        if a.get("crawler.llm.budget_exceeded"):
            findings.append(_finding("llm_budget_exceeded", agent, None,
                                     f"the planner used all {a.get('crawler.llm.max_steps')} steps without finishing; "
                                     "the rules covered the rest", s, max_steps=a.get("crawler.llm.max_steps")))
        if median and s["duration_ms"] and s["duration_ms"] > THRESHOLDS["slow_run_ratio"] * median:
            findings.append(_finding("slow_run", agent, None,
                                     f"crawl took {s['duration_ms'] / 1000:.1f} s, {s['duration_ms'] / median:.0f}x the median "
                                     f"of {median / 1000:.1f} s", s, duration_ms=s["duration_ms"], median_ms=median))
    return findings


class ObservabilityAgent:
    """Ingests /ai/agents/* span logs, links them to the registry and stores behaviour findings."""

    def __init__(self, session, conn, run_id):
        self.session, self.conn, self.run_id = session, conn, run_id

    def _approval_threshold(self, row):
        if row["service"] != "lambda":
            return None
        try:
            env = self.session.client("lambda").get_function_configuration(FunctionName=row["resource_arn"])
        except Exception:
            return None
        value = env.get("Environment", {}).get("Variables", {}).get("APPROVAL_THRESHOLD")
        return float(value) if value else None

    def run(self) -> dict:
        conn, logs = self.conn, self.session.client("logs")
        started = datetime.now(timezone.utc)
        trace_id, root_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
        window_start = started - timedelta(hours=config.log_lookback_hours)
        since = int(window_start.timestamp() * 1000)

        rows = store.active_registry(conn)
        by_log_group = {r["log_group"]: r for r in rows if r["log_group"]}
        by_name = {r["name"]: r for r in rows}

        spans, registry, own_spans = [], {}, []
        for group in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=config.agent_log_prefix):
            for g in group["logGroups"]:
                step_start = datetime.now(timezone.utc)
                pages = logs.get_paginator("filter_log_events").paginate(logGroupName=g["logGroupName"], startTime=since)
                # CloudWatch can hold the same span twice (exporter retries, a log group recreated with its old events)
                group_spans = list({(s["trace_id"], s["span_id"]): s for s in
                                    (normalise(e) for page in pages for e in page["events"])}.values())
                for agent in {s["attributes"]["gen_ai.agent.name"] for s in group_spans}:
                    row = by_log_group.get(g["logGroupName"]) or by_name.get(agent)
                    if row:
                        registry[agent] = {**row, "approval_threshold": self._approval_threshold(row)}
                spans += group_spans
                own_spans.append(make_span(AGENT_NAME, trace_id, root_id, "execute_tool", "execute_tool read_log_group",
                                           step_start, {"gen_ai.tool.name": "read_log_group",
                                                        "observability.log_group": g["logGroupName"],
                                                        "observability.spans": len(group_spans)}))

        per_registry = defaultdict(list)
        for span in spans:
            row = registry.get(span["attributes"]["gen_ai.agent.name"])
            per_registry[row["id"] if row else None].append(span)
        for registry_id, group_spans in per_registry.items():
            store.insert_spans(conn, group_spans, self.run_id, registry_id)
        store.mark_monitored(conn, [rid for rid in per_registry if rid])

        step_start = datetime.now(timezone.utc)
        crawler_spans = store.agent_spans(conn, CRAWLER_AGENT, window_start)
        findings = detect_anomalies(spans, registry) + detect_crawler_anomalies(crawler_spans)
        store.replace_anomalies(conn, self.run_id, findings)
        own_spans.append(make_span(AGENT_NAME, trace_id, root_id, "execute_tool", "execute_tool detect_anomalies",
                                   step_start, {"gen_ai.tool.name": "detect_anomalies", "observability.findings": len(findings)}))
        own_spans.insert(0, make_span(AGENT_NAME, trace_id, None, "invoke_agent", f"invoke_agent {AGENT_NAME}", started,
                                      {"observability.spans": len(spans), "observability.findings": len(findings)},
                                      span_id=root_id))
        store.insert_spans(conn, own_spans, self.run_id)
        conn.commit()

        counts = defaultdict(int)
        for span in spans:
            counts[span["attributes"]["gen_ai.agent.name"]] += 1
        return {"spans": len(spans), "spans_per_agent": dict(sorted(counts.items())), "findings": findings}
