"""All registry, run and trace SQL. Callers own the connection and the transaction."""
from datetime import datetime

from psycopg import sql
from psycopg.types.json import Jsonb

JSONB_COLUMNS = {"evidence", "models", "tools", "data_sources", "risk_factors", "controls",
                 "unused_ai_permissions", "tags"}


def start_run(conn, mode, regions) -> int:
    return conn.execute("INSERT INTO discovery_runs (mode, regions) VALUES (%s, %s) RETURNING id",
                        (mode, regions)).fetchone()["id"]


def finish_run(conn, run_id, counts=None, error=None):
    """counts=None marks the run failed; otherwise it succeeded (error may still note partial failures)."""
    counts = counts or {}
    conn.execute(
        """UPDATE discovery_runs SET finished_at = now(), status = %s, error = %s,
               resources_scanned = %s, ai_systems_found = %s, agents_found = %s
           WHERE id = %s""",
        ("failed" if not counts else "succeeded", error, counts.get("resources_scanned", 0),
         counts.get("ai_systems_found", 0), counts.get("agents_found", 0), run_id),
    )


def upsert_registry(conn, record):
    columns = list(record)
    values = [Jsonb(record[c]) if c in JSONB_COLUMNS else record[c] for c in columns]
    updates = [sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in columns if c not in ("id", "status")]
    conn.execute(
        sql.SQL("INSERT INTO ai_registry ({cols}) VALUES ({vals}) ON CONFLICT (id) DO UPDATE SET {updates}, "
                "last_seen_at = now(), status = 'active'").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
            vals=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            updates=sql.SQL(", ").join(updates),
        ),
        values,
    )


def retire_unseen(conn, run_id) -> int:
    return conn.execute("UPDATE ai_registry SET status = 'retired' WHERE status = 'active' "
                        "AND last_run_id IS DISTINCT FROM %s", (run_id,)).rowcount


def insert_spans(conn, spans, run_id, registry_id=None):
    """spans use the OTel shape written to the agent log groups."""
    rows = []
    for s in spans:
        a = s["attributes"]
        start = datetime.fromisoformat(s["start_time"])
        end = datetime.fromisoformat(s["end_time"]) if s.get("end_time") else None
        rows.append((
            run_id, registry_id, a["gen_ai.agent.name"], s["trace_id"], s["span_id"], s.get("parent_span_id"),
            a.get("gen_ai.operation.name"), s.get("name"), a.get("gen_ai.request.model"), a.get("gen_ai.tool.name"),
            start, end, s.get("duration_ms", int((end - start).total_seconds() * 1000) if end else None),
            a.get("gen_ai.usage.input_tokens"), a.get("gen_ai.usage.output_tokens"),
            s.get("cost_usd"), s.get("status"), a.get("error.type"), Jsonb(a),
        ))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO agent_traces (run_id, registry_id, agent_name, trace_id, span_id, parent_span_id, operation,
                   span_name, model, tool_name, started_at, ended_at, duration_ms, input_tokens, output_tokens,
                   cost_usd, status, error_type, attributes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (trace_id, span_id) DO NOTHING""",
            rows,
        )


def active_registry(conn):
    return conn.execute("SELECT id, name, service, resource_arn, log_group, tools FROM ai_registry "
                        "WHERE status = 'active'").fetchall()


def mark_monitored(conn, registry_ids):
    conn.execute("UPDATE ai_registry SET monitored = true WHERE id = ANY(%s)", (list(registry_ids),))


def replace_anomalies(conn, run_id, findings):
    """Findings are recomputed over the whole lookback window each run, so the table holds only the latest set."""
    conn.execute("DELETE FROM agent_anomalies")
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO agent_anomalies (run_id, registry_id, agent_name, rule, severity, trace_id, span_id,
                   description, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, f["registry_id"], f["agent_name"], f["rule"], f["severity"], f["trace_id"], f["span_id"],
              f["description"], Jsonb(f["details"])) for f in findings],
        )
    conn.execute("UPDATE discovery_runs SET findings_count = %s WHERE id = %s", (len(findings), run_id))


def list_registry(conn):
    return conn.execute(
        """SELECT name, category, risk_level, risk_score, autonomy_level, human_oversight, owner, status
           FROM ai_registry ORDER BY status, risk_score DESC, name"""
    ).fetchall()


def get_run(conn, run_id):
    return conn.execute("SELECT * FROM discovery_runs WHERE id = %s", (run_id,)).fetchone()
