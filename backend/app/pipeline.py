"""crawl -> observe -> recommend in one process, so moto's in-memory estate (mock mode) is shared by every step."""
from contextlib import contextmanager

from app import aws
from app.config import config
from app.crawler.agent import CrawlerAgent
from app.llm import make_llm
from app.observability.agent import ObservabilityAgent
from app.recommend.engine import recommend_all

LOCK_KEY = 4242002
STEPS = ("crawl", "observe", "recommend")


class DiscoveryRunning(Exception):
    pass


@contextmanager
def _aws_session(mode):
    if mode == "mock":
        with aws.simulated_aws():
            yield aws.session()
    else:
        yield aws.session()


def run_discovery(conn, mode, until="recommend", llm=None) -> dict:
    """Runs the steps up to and including `until`. Raises DiscoveryRunning if another run holds the lock."""
    steps = STEPS[:STEPS.index(until) + 1]
    # session-level lock: held across the steps' own commits, released in finally
    if not conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (LOCK_KEY,)).fetchone()["locked"]:
        conn.rollback()
        raise DiscoveryRunning("a discovery run is already in progress")
    try:
        # built before the mock starts, so in mock mode it still holds real credentials
        llm = llm or (make_llm(aws.session()) if config.llm_enabled else None)
        with _aws_session(mode) as session:
            crawl = CrawlerAgent(session, aws.lambda_code_fetcher(mode), conn, llm).run()
            observe = ObservabilityAgent(session, conn, crawl["run_id"]).run() if "observe" in steps else None
            recommendations = recommend_all(conn, crawl["run_id"]) if "recommend" in steps else None
        return {"run_id": crawl["run_id"], "mode": mode, "crawl": crawl, "observe": observe,
                "recommendations": recommendations}
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        conn.commit()
