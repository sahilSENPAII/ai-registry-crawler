"""All registry, run and trace SQL. Callers own the connection and the transaction."""
from datetime import datetime

from psycopg import sql
from psycopg.types.json import Jsonb

from app.tracing import CRAWLER_AGENT

JSONB_COLUMNS = {"evidence", "llm_verdict", "models", "tools", "data_sources", "risk_factors", "controls",
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


def add_control(conn, registry_ids, control) -> int:
    """Appends a control to these systems' controls unless they already have it."""
    return conn.execute("UPDATE ai_registry SET controls = controls || %s WHERE id = ANY(%s) AND NOT controls @> %s",
                        (Jsonb([control]), list(registry_ids), Jsonb([{"ref": control["ref"]}]))).rowcount


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
    return conn.execute("SELECT id, name, service, resource_arn, log_group, tools, category, task_type, models, "
                        "data_classification, risk_level FROM ai_registry WHERE status = 'active' ORDER BY name").fetchall()


def agent_spans(conn, agent_name, since):
    return conn.execute("SELECT * FROM agent_traces WHERE agent_name = %s AND started_at >= %s ORDER BY started_at, id",
                        (agent_name, since)).fetchall()


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


def observed_usage(conn, since) -> dict:
    """registry_id -> {"runs", "input_tokens", "output_tokens"} totals for traces since `since`."""
    rows = conn.execute(
        """SELECT registry_id, count(DISTINCT trace_id) AS runs, coalesce(sum(input_tokens), 0) AS input_tokens,
                  coalesce(sum(output_tokens), 0) AS output_tokens
           FROM agent_traces WHERE registry_id IS NOT NULL AND started_at >= %s GROUP BY registry_id""",
        (since,)).fetchall()
    return {r.pop("registry_id"): r for r in rows}


def upsert_recommendation(conn, run_id, rec):
    record = {**rec, "run_id": run_id, "alternatives": Jsonb(rec["alternatives"])}
    columns = list(record)
    conn.execute(
        sql.SQL("INSERT INTO model_recommendations ({cols}) VALUES ({vals}) ON CONFLICT (registry_id) "
                "DO UPDATE SET {updates}, created_at = now()").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
            vals=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            updates=sql.SQL(", ").join(sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c))
                                       for c in columns if c != "registry_id"),
        ),
        [record[c] for c in columns],
    )


def list_registry(conn):
    return conn.execute(
        """SELECT name, category, risk_level, risk_score, autonomy_level, human_oversight, owner, status
           FROM ai_registry ORDER BY status, risk_score DESC, name"""
    ).fetchall()


def get_run(conn, run_id):
    return conn.execute("SELECT * FROM discovery_runs WHERE id = %s", (run_id,)).fetchone()


# --- dashboard API reads ---

INTERNAL = "coalesce((attributes->>'internal')::boolean, false)"
SEVERITY_ORDER = "array_position(ARRAY['critical','high','medium','low'], severity)"


def ping(conn):
    conn.execute("SELECT 1")


def registry_count(conn) -> int:
    return conn.execute("SELECT count(*) AS n FROM ai_registry").fetchone()["n"]


def list_runs(conn, limit=20):
    return conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


def summary(conn):
    def counts(query):
        return {r["key"]: r["n"] for r in conn.execute(query).fetchall()}

    active = "FROM ai_registry WHERE status = 'active'"
    totals = conn.execute(f"SELECT count(*) AS systems, count(*) FILTER (WHERE is_agent) AS agents, "
                          f"count(*) FILTER (WHERE monitored) AS monitored {active}").fetchone()
    cost = conn.execute(
        """SELECT coalesce(sum(m.est_monthly_cost_current), 0) AS current,
                  coalesce(sum(m.est_monthly_cost_recommended), 0) AS recommended
           FROM model_recommendations m JOIN ai_registry r ON r.id = m.registry_id WHERE r.status = 'active'"""
    ).fetchone()
    return {
        **totals,
        "by_category": counts(f"SELECT category AS key, count(*) AS n {active} GROUP BY category"),
        "by_risk_level": counts(f"SELECT risk_level AS key, count(*) AS n {active} GROUP BY risk_level"),
        "findings_by_severity": counts("SELECT severity AS key, count(*) AS n FROM agent_anomalies GROUP BY severity"),
        "monthly_cost": cost,
        "last_run": conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 1").fetchone(),
    }


def list_systems(conn, category=None, risk_level=None, status=None, include_retired=False):
    return conn.execute(
        """SELECT id, name, service, region, category, is_agent, risk_level, risk_score, autonomy_level,
                  human_oversight, owner, environment, data_classification, monitored, status, last_seen_at
           FROM ai_registry
           WHERE (%(category)s::text IS NULL OR category = %(category)s)
             AND (%(risk_level)s::text IS NULL OR risk_level = %(risk_level)s)
             AND (%(status)s::text IS NULL OR status = %(status)s)
             AND (%(status)s::text IS NOT NULL OR %(include_retired)s OR status = 'active')
           ORDER BY risk_score DESC, name""",
        {"category": category, "risk_level": risk_level, "status": status, "include_retired": include_retired},
    ).fetchall()


def get_system(conn, registry_id):
    system = conn.execute("SELECT * FROM ai_registry WHERE id = %s", (registry_id,)).fetchone()
    if system is None:
        return None
    return {
        **system,
        "recommendation": conn.execute("SELECT * FROM model_recommendations WHERE registry_id = %s",
                                       (registry_id,)).fetchone(),
        "findings": conn.execute(f"SELECT * FROM agent_anomalies WHERE registry_id = %s ORDER BY {SEVERITY_ORDER}, id",
                                 (registry_id,)).fetchall(),
        "run_stats": conn.execute(
            """SELECT count(DISTINCT trace_id) AS runs, count(*) AS spans,
                      coalesce(sum(input_tokens), 0) + coalesce(sum(output_tokens), 0) AS tokens,
                      coalesce(sum(cost_usd), 0) AS cost_usd,
                      count(DISTINCT trace_id) FILTER (WHERE status = 'error') AS error_runs,
                      max(started_at) AS last_seen
               FROM agent_traces WHERE registry_id = %s""", (registry_id,)).fetchone(),
    }


def list_agents(conn, include_internal=False):
    return conn.execute(
        f"""SELECT t.agent_name AS name, max(t.registry_id) AS registry_id, max(t.registry_id) IS NOT NULL AS registered,
                   bool_or({INTERNAL}) AS internal, count(DISTINCT t.trace_id) AS runs, count(*) AS spans,
                   coalesce(sum(t.input_tokens), 0) + coalesce(sum(t.output_tokens), 0) AS tokens,
                   coalesce(sum(t.cost_usd), 0) AS cost_usd,
                   count(DISTINCT t.trace_id) FILTER (WHERE t.status = 'error') AS error_runs,
                   (SELECT count(*) FROM agent_anomalies a WHERE a.agent_name = t.agent_name) AS findings,
                   max(t.started_at) AS last_seen
            FROM agent_traces t
            GROUP BY t.agent_name
            HAVING %s OR NOT bool_or({INTERNAL}) OR t.agent_name = %s
            ORDER BY registered, name""",
        (include_internal, CRAWLER_AGENT),
    ).fetchall()


def agent_runs(conn, agent_name, limit=100):
    return conn.execute(
        """SELECT trace_id, min(started_at) AS started_at, max(ended_at) AS ended_at,
                  (extract(epoch FROM max(ended_at) - min(started_at)) * 1000)::int AS duration_ms, count(*) AS spans,
                  coalesce(sum(input_tokens), 0) + coalesce(sum(output_tokens), 0) AS tokens,
                  coalesce(sum(cost_usd), 0) AS cost_usd,
                  CASE WHEN bool_or(status = 'error') THEN 'error' ELSE 'ok' END AS status
           FROM agent_traces WHERE agent_name = %s
           GROUP BY trace_id ORDER BY started_at DESC LIMIT %s""",
        (agent_name, limit),
    ).fetchall()


def trace_spans(conn, trace_id):
    return conn.execute("SELECT * FROM agent_traces WHERE trace_id = %s ORDER BY started_at, parent_span_id NULLS FIRST, id",
                        (trace_id,)).fetchall()


def list_findings(conn, severity=None, agent=None):
    return conn.execute(
        f"""SELECT * FROM agent_anomalies
            WHERE (%(severity)s::text IS NULL OR severity = %(severity)s)
              AND (%(agent)s::text IS NULL OR agent_name = %(agent)s)
            ORDER BY {SEVERITY_ORDER}, agent_name, id""",
        {"severity": severity, "agent": agent},
    ).fetchall()


def list_recommendations(conn, include_retired=False):
    return conn.execute(
        """SELECT r.name, r.category, r.risk_level, m.* FROM model_recommendations m
           JOIN ai_registry r ON r.id = m.registry_id
           WHERE %s OR r.status = 'active' ORDER BY r.name""",
        (include_retired,),
    ).fetchall()
