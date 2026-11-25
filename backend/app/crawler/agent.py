import uuid
from datetime import datetime, timezone

from app import store
from app.config import config
from app.crawler.classifier import classify
from app.crawler.collectors import collectors
from app.crawler.registry import build_record
from app.tracing import make_span

AGENT_NAME = "aws-ai-crawler"


class CrawlerAgent:
    """collect -> classify -> register, tracing each step as OTel GenAI spans."""

    def __init__(self, session, fetch_code, conn):
        self.session, self.fetch_code, self.conn = session, fetch_code, conn

    def run(self) -> dict:
        conn = self.conn
        run_id = store.start_run(conn, config.discovery_mode, [self.session.region_name])
        conn.commit()
        trace_id, root_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
        started = datetime.now(timezone.utc)
        spans, errors = [], []
        counts = {"resources_scanned": 0, "ai_systems_found": 0, "agents_found": 0}
        try:
            for collect in collectors(self.fetch_code):
                step_start = datetime.now(timezone.utc)
                try:
                    resources = collect(self.session)
                except Exception as e:
                    errors.append(f"{collect.__name__}: {e}")
                    spans.append(make_span(AGENT_NAME, trace_id, root_id, "execute_tool", f"execute_tool {collect.__name__}",
                                           step_start, {"gen_ai.tool.name": collect.__name__}, "error", type(e).__name__))
                    continue
                ai = agents = 0
                for resource in resources:
                    classification = classify(resource)
                    if classification.is_ai:
                        store.upsert_registry(conn, build_record(resource, classification, run_id))
                        ai += 1
                        agents += classification.is_agent
                counts["resources_scanned"] += len(resources)
                counts["ai_systems_found"] += ai
                counts["agents_found"] += agents
                spans.append(make_span(AGENT_NAME, trace_id, root_id, "execute_tool", f"execute_tool {collect.__name__}",
                                       step_start, {
                    "gen_ai.tool.name": collect.__name__, "crawler.resources": len(resources),
                    "crawler.ai_systems": ai, "crawler.agents": agents,
                }))
            # a failed collector hides its resources, so retiring now would retire systems that still exist
            retired = 0 if errors else store.retire_unseen(conn, run_id)
            store.finish_run(conn, run_id, counts, "; ".join(errors) or None)
            spans.insert(0, make_span(AGENT_NAME, trace_id, None, "invoke_agent", f"invoke_agent {AGENT_NAME}", started,
                                      {**{f"crawler.{k}": v for k, v in counts.items()}, "crawler.retired": retired}))
            store.insert_spans(conn, spans, run_id)
            conn.commit()
        except Exception as e:
            conn.rollback()
            store.finish_run(conn, run_id, error=f"{type(e).__name__}: {e}")
            spans.insert(0, make_span(AGENT_NAME, trace_id, None, "invoke_agent", f"invoke_agent {AGENT_NAME}", started,
                                      {}, "error", type(e).__name__))
            store.insert_spans(conn, spans, run_id)
            conn.commit()
            raise
        return {"run_id": run_id, **counts, "retired": retired, "errors": errors}
