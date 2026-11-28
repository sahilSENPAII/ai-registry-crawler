import json

import pytest

from app.observability.agent import detect_anomalies, detect_crawler_anomalies, normalise

REFUND = {"id": "r1", "tools": [{"name": "lookup_order"}, {"name": "issue_refund"}], "approval_threshold": 500.0}


def span(op, agent="refund-agent", trace="t1", n=0, status="ok", **attrs):
    return {
        "trace_id": trace, "span_id": f"{trace}-{n}", "parent_span_id": None if op == "invoke_agent" else f"{trace}-root",
        "name": op, "start_time": f"2026-11-20T10:00:{n:02d}.000Z", "end_time": f"2026-11-20T10:00:{n:02d}.500Z",
        "status": status, "attributes": {"gen_ai.operation.name": op, "gen_ai.agent.name": agent, **attrs},
    }


def tool(name, n, agent="refund-agent", trace="t1", **args):
    return span("execute_tool", agent, trace, n, **{"gen_ai.tool.name": name,
                                                    "gen_ai.tool.call.arguments": json.dumps(args)})


def approval(action, amount, decision, n, agent="refund-agent", trace="t1"):
    return span("human_approval", agent, trace, n, **{"approval.action": action, "approval.amount": amount,
                                                      "approval.decision": decision})


def chat(n, tokens, agent="refund-agent", trace="t1"):
    return span("chat", agent, trace, n, **{"gen_ai.request.model": "gpt-4o", "gen_ai.usage.input_tokens": tokens,
                                            "gen_ai.usage.output_tokens": 0})


def rules(findings):
    return [f["rule"] for f in findings]


def test_normalise_redacts_and_prices():
    raw = span("chat", **{"user.email": "a@b.com", "customer_phone": "555", "gen_ai.request.model": "gpt-4o",
                          "gen_ai.usage.input_tokens": 1_000_000, "gen_ai.usage.output_tokens": 100_000,
                          "gen_ai.tool.call.arguments": json.dumps({"card_number": "4111", "order_id": "O1"})})
    s = normalise({"message": json.dumps(raw)})
    assert s["attributes"]["user.email"] == s["attributes"]["customer_phone"] == "[redacted]"
    assert json.loads(s["attributes"]["gen_ai.tool.call.arguments"]) == {"card_number": "[redacted]", "order_id": "O1"}
    assert s["duration_ms"] == 500
    assert s["cost_usd"] == pytest.approx(2.50 + 1.00)


def test_unregistered_agent():
    findings = detect_anomalies([span("invoke_agent", agent="shadow"), chat(1, 100, agent="shadow")], {})
    assert rules(findings) == ["unregistered_agent"]
    assert findings[0]["registry_id"] is None and "2 spans in 1 runs" in findings[0]["description"]


def test_internal_agent_is_never_unregistered():
    internal = span("invoke_agent", agent="aws-ai-crawler", internal=True)
    assert detect_anomalies([internal], {}) == []


def test_undeclared_tool():
    findings = detect_anomalies([tool("lookup_order", 1), tool("export_contacts_csv", 2)], {"refund-agent": REFUND})
    assert rules(findings) == ["undeclared_tool"]
    assert findings[0]["details"]["tool"] == "export_contacts_csv"


def test_side_effect_without_approval():
    findings = detect_anomalies([tool("issue_refund", 1, amount=1200.0), tool("issue_refund", 2, trace="t2", amount=80.0)],
                                {"refund-agent": REFUND})
    assert rules(findings) == ["side_effect_without_approval"]
    assert findings[0]["description"] == "issue_refund of $1,200 without approval (threshold $500)"
    assert findings[0]["severity"] == "critical"


def test_approved_large_purchase_order_is_not_flagged():
    procurement = {"id": "p1", "tools": [{"name": "create_purchase_order"}], "approval_threshold": 10_000.0}
    approved = [approval("create_purchase_order", 25000.0, "approved", 1, agent="procurement-agent"),
                tool("create_purchase_order", 2, agent="procurement-agent", amount=25000.0)]
    assert detect_anomalies(approved, {"procurement-agent": procurement}) == []
    late_approval = [tool("create_purchase_order", 1, agent="procurement-agent", amount=25000.0),
                     approval("create_purchase_order", 25000.0, "approved", 2, agent="procurement-agent")]
    assert rules(detect_anomalies(late_approval, {"procurement-agent": procurement})) == ["side_effect_without_approval"]


def test_tool_loop():
    loop = [tool("lookup_order", i) for i in range(4)]
    findings = detect_anomalies(loop, {"refund-agent": REFUND})
    assert rules(findings) == ["tool_loop"] and findings[0]["details"]["calls"] == 4
    assert detect_anomalies(loop[:3], {"refund-agent": REFUND}) == []


def test_token_spike():
    spans = [chat(0, 6000, trace=f"t{i}") for i in range(5)] + [chat(0, 40_000, trace="big")]
    findings = detect_anomalies(spans, {"refund-agent": REFUND})
    assert rules(findings) == ["token_spike"] and findings[0]["trace_id"] == "big"
    small = [chat(0, 100, trace=f"t{i}") for i in range(5)] + [chat(0, 15_000, trace="big")]
    assert detect_anomalies(small, {"refund-agent": REFUND}) == []  # 150x the median but under 20k


def test_run_error():
    spans = [span("invoke_agent", n=0, status="error", **{"error.type": "RateLimitError"}),
             span("chat", n=1, status="error", **{"error.type": "RateLimitError"})]
    findings = detect_anomalies(spans, {"refund-agent": REFUND})
    assert rules(findings) == ["run_error"] and findings[0]["details"]["error_type"] == "RateLimitError"
    assert findings[0]["span_id"] == "t1-1"


def test_approval_denied():
    findings = detect_anomalies([approval("issue_refund", 750.0, "denied", 1)], {"refund-agent": REFUND})
    assert rules(findings) == ["approval_denied"] and findings[0]["severity"] == "low"
    assert findings[0]["description"] == "approver denied issue_refund of $750; the agent stopped"


def crawler_span(op, duration_ms=1000, tool=None, status="ok", **attrs):
    return {"trace_id": f"t{duration_ms}", "span_id": f"s{duration_ms}{tool}", "operation": op, "tool_name": tool,
            "duration_ms": duration_ms, "status": status, "error_type": "RuntimeError" if status == "error" else None,
            "attributes": attrs}


def test_crawler_rules():
    spans = [
        crawler_span("invoke_agent", 1000), crawler_span("invoke_agent", 1200), crawler_span("invoke_agent", 5000),
        crawler_span("invoke_agent", 900, **{"crawler.llm.budget_exceeded": True, "crawler.llm.max_steps": 12}),
        crawler_span("execute_tool", tool="collect_sagemaker", status="error"),
        crawler_span("execute_tool", tool="record_verdict",
                     **{"crawler.resource": "order-fulfilment", "crawler.verdict.rejected": ["unknown category 'x'"]}),
    ]
    findings = {f["rule"]: f for f in detect_crawler_anomalies(spans)}
    assert sorted(findings) == ["collector_failed", "llm_budget_exceeded", "llm_verdict_rejected", "slow_run"]
    assert findings["slow_run"]["details"]["duration_ms"] == 5000 and findings["slow_run"]["severity"] == "low"
    assert findings["collector_failed"]["description"] == "collect_sagemaker failed with RuntimeError"
    assert all(f["agent_name"] == "aws-ai-crawler" and f["registry_id"] is None for f in findings.values())


def test_steady_crawls_are_not_slow():
    assert detect_crawler_anomalies([crawler_span("invoke_agent", d) for d in (1000, 1100, 2900)]) == []
