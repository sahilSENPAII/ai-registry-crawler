import logging
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import config

log = logging.getLogger(__name__)
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# child tables first; also the order the CLI prints them in
TABLES = ["model_recommendations", "agent_anomalies", "agent_traces", "ai_registry", "discovery_runs"]


def connect() -> psycopg.Connection:
    # connect_timeout so /health can't hang on an unreachable host
    return psycopg.connect(config.database_url, row_factory=dict_row, options="-c TimeZone=UTC", connect_timeout=5)


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA_PATH.read_text())


def reset_data() -> None:
    with connect() as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")


def wait_for_db(retries: int = 10, delay: float = 2) -> bool:
    for attempt in range(1, retries + 1):
        try:
            with connect() as conn:
                conn.execute("SELECT 1")
            log.info("database is ready")
            return True
        except psycopg.Error as e:
            log.warning("database not ready (attempt %d/%d): %s", attempt, retries, e)
            time.sleep(delay)
    log.error("database still unavailable after %d attempts; starting anyway", retries)
    return False
