import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import db, store
from app.config import config
from app.pipeline import DiscoveryRunning, run_discovery

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: never crash on a slow/missing DB; /health reports it instead
    if db.wait_for_db():
        db.init_schema()
        if config.auto_discover and config.discovery_mode == "mock":
            try:
                with db.connect() as conn:
                    if store.registry_count(conn) == 0:
                        log.info("registry is empty, running discovery once")
                        run_discovery(conn, "mock")
            except Exception:
                log.exception("startup discovery failed; the API is still serving")
    yield


app = FastAPI(title="AI Registry Crawler", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, allow_methods=["*"], allow_headers=["*"])


def get_conn():
    with db.connect() as conn:
        yield conn


@app.get("/health")
def health():
    try:
        with db.connect() as conn:
            store.ping(conn)
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "degraded", "db": str(e)}


@app.post("/discovery/run")
def discovery_run(conn=Depends(get_conn)):
    if config.discovery_mode != "mock":
        raise HTTPException(403, "discovery from the API runs only in mock mode; use the CLI for a real account")
    try:
        result = run_discovery(conn, "mock")
    except DiscoveryRunning as e:
        raise HTTPException(409, str(e))
    return {
        "run_id": result["run_id"], "mode": result["mode"],
        **{k: result["crawl"][k] for k in ("resources_scanned", "ai_systems_found", "agents_found", "retired", "errors")},
        "spans": result["observe"]["spans"], "findings": len(result["observe"]["findings"]),
        "recommendations": len(result["recommendations"]),
    }


@app.get("/runs")
def runs(limit: int = 20, conn=Depends(get_conn)):
    return store.list_runs(conn, limit)


@app.get("/summary")
def summary(conn=Depends(get_conn)):
    return store.summary(conn)


@app.get("/systems")
def systems(category: str | None = None, risk_level: str | None = None, status: str | None = None,
            include_retired: bool = False, conn=Depends(get_conn)):
    return store.list_systems(conn, category, risk_level, status, include_retired)


@app.get("/systems/{registry_id}")
def system(registry_id: str, conn=Depends(get_conn)):
    found = store.get_system(conn, registry_id)
    if found is None:
        raise HTTPException(404, f"no AI system {registry_id}")
    return found


@app.get("/agents")
def agents(include_internal: bool = False, conn=Depends(get_conn)):
    return store.list_agents(conn, include_internal)


@app.get("/agents/{name}/runs")
def agent_runs(name: str, limit: int = 100, conn=Depends(get_conn)):
    return store.agent_runs(conn, name, limit)


@app.get("/traces/{trace_id}")
def trace(trace_id: str, conn=Depends(get_conn)):
    spans = store.trace_spans(conn, trace_id)
    if not spans:
        raise HTTPException(404, f"no trace {trace_id}")
    return spans


@app.get("/findings")
def findings(severity: str | None = None, agent: str | None = None, conn=Depends(get_conn)):
    return store.list_findings(conn, severity, agent)


@app.get("/recommendations")
def recommendations(include_retired: bool = False, conn=Depends(get_conn)):
    return store.list_recommendations(conn, include_retired)
