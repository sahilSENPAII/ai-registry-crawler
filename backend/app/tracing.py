import uuid
from datetime import datetime, timezone


def make_span(agent, trace_id, parent_id, operation, name, start, attrs, status="ok", error_type=None):
    """A span in the same OTel GenAI shape as the agent logs, marked internal so it is never a shadow agent."""
    attributes = {"gen_ai.operation.name": operation, "gen_ai.agent.name": agent, "internal": True, **attrs}
    if error_type:
        attributes["error.type"] = error_type
    return {
        "trace_id": trace_id, "span_id": uuid.uuid4().hex[:16], "parent_span_id": parent_id, "name": name,
        "start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
        "status": status, "attributes": attributes,
    }
