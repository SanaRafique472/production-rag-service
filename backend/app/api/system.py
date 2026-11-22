"""Health, monitoring metrics and stored evaluation results."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_container, get_db
from app.errors import NotFoundError
from app.schemas import HealthOut, MetricsOut
from app.services import metrics_service
from app.services.container import Container

router = APIRouter(tags=["system"])
EVAL_RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"


@router.get("/health", response_model=HealthOut)
async def health(
    request: Request, db: AsyncSession = Depends(get_db), c: Container = Depends(get_container)
) -> HealthOut:
    """Deep health check: database, vector store and whether provider keys are set."""
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # report, don't crash
        checks["database"] = f"error: {type(exc).__name__}"
    checks["vector_store"] = "ok" if await c.store.ping() else "error"
    if c.mode == "fake":
        checks["providers"] = "fake (test mode)"
    else:
        s = c.settings
        checks["cohere"] = "configured" if s.cohere_api_key else "missing COHERE_API_KEY"
        checks["anthropic"] = "configured" if s.anthropic_api_key else "missing ANTHROPIC_API_KEY"
    healthy = checks["database"] == "ok" and checks["vector_store"] == "ok"
    return HealthOut(status="ok" if healthy else "degraded", mode=c.mode, checks=checks)


@router.get("/metrics", response_model=MetricsOut, dependencies=[Depends(current_user)])
async def metrics(hours: int = Query(24, ge=1, le=24 * 30), db: AsyncSession = Depends(get_db)) -> dict:
    """Latency percentiles, tokens, cost, error and refusal rates for the dashboard."""
    return await metrics_service.summary(db, hours)


@router.get("/evals", dependencies=[Depends(current_user)], tags=["evaluation"])
async def list_eval_runs() -> list[dict]:
    """Summaries of stored evaluation runs (written by ``make eval``)."""
    runs = []
    for path in sorted(EVAL_RESULTS_DIR.glob("*.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        runs.append({"run_id": path.stem, **{k: data.get(k) for k in ("created_at", "mode", "configs", "summary")}})
    return runs


@router.get("/evals/{run_id}", dependencies=[Depends(current_user)], tags=["evaluation"])
async def get_eval_run(run_id: str) -> dict:
    """Full results of one evaluation run, including per-question rows."""
    path = EVAL_RESULTS_DIR / f"{Path(run_id).name}.json"
    if not path.is_file():
        raise NotFoundError("Evaluation run not found.")
    return json.loads(path.read_text(encoding="utf-8"))
