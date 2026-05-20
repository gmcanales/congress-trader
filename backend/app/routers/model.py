"""Model endpoints: run the parameterized train/test analysis and persist
every run (params + scores + full results) as a traceable AnalysisRun."""
import itertools
import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app import models
from app.services import backtest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model", tags=["model"])


class AnalyzeRequest(BaseModel):
    dataset_id: int
    train_start: date = date(2021, 1, 1)
    train_end: date = date(2024, 12, 31)
    test_start: date = date(2025, 1, 1)
    test_end: date = date(2025, 12, 31)
    date_field: str = "filing_date"
    event_horizons: list[int] = [5, 21, 63]
    hold_days: list[int] = [21, 63]
    min_members: list[int] = [1, 2, 3]
    benchmark: str = "SPY"  # ticker excess returns are measured against (SPY cap-weight, RSP equal-weight)
    leadership_only: bool = False  # restrict to trades made while member held a leadership/chair position


def _extract_scores(result: dict) -> dict:
    """Flat, cross-run-comparable headline metrics."""
    st = result["selected_train_result"]
    oos = result["out_of_sample_test"]
    return {
        "date_field": result["date_field"],
        "benchmark": result.get("benchmark", "SPY"),
        "leadership_only": result.get("leadership_only", False),
        "selected_params": result["selected_params"],
        "train_excess": st["excess_return"],
        "oos_excess": oos["excess_return"],
        "oos_strategy_return": oos["strategy_return"],
        "oos_benchmark_return": oos["benchmark_return"],
        "overfit_gap": round(st["excess_return"] - oos["excess_return"], 4),
    }


def _run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(models.AnalysisRun, run_id)
        p = run.params
        horizons = {f"{d}d": d for d in p["event_horizons"]}
        grid = [{"horizon_days": h, "min_members": m}
                for h, m in itertools.product(p["hold_days"], p["min_members"])]
        result = backtest.train_test_analysis(
            date.fromisoformat(p["train_start"]), date.fromisoformat(p["train_end"]),
            date.fromisoformat(p["test_start"]), date.fromisoformat(p["test_end"]),
            date_field=p["date_field"], param_grid=grid, horizons=horizons,
            benchmark=p.get("benchmark", "SPY"),
            leadership_only=p.get("leadership_only", False),
        )
        run.full_results = result
        run.scores = _extract_scores(result)
        run.status = "done"
        db.commit()
    except Exception as exc:
        logger.error(f"analysis run {run_id} failed: {exc}")
        run = db.get(models.AnalysisRun, run_id)
        if run:
            run.status = "error"
            run.error = str(exc)
            db.commit()
    finally:
        db.close()


def _serialize(run: models.AnalysisRun, include_full: bool = False) -> dict:
    out = {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "tool": run.tool,
        "status": run.status,
        "error": run.error,
        "params": run.params,
        "scores": run.scores,
    }
    if include_full:
        out["full_results"] = run.full_results
    return out


@router.post("/run")
def run_model(req: AnalyzeRequest, background_tasks: BackgroundTasks,
              db: Session = Depends(get_db)):
    if not db.get(models.Dataset, req.dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")

    params = {
        "train_start": req.train_start.isoformat(),
        "train_end": req.train_end.isoformat(),
        "test_start": req.test_start.isoformat(),
        "test_end": req.test_end.isoformat(),
        "date_field": req.date_field,
        "event_horizons": req.event_horizons,
        "hold_days": req.hold_days,
        "min_members": req.min_members,
        "benchmark": req.benchmark,
        "leadership_only": req.leadership_only,
    }
    run = models.AnalysisRun(
        dataset_id=req.dataset_id, tool="train_test_analysis",
        status="running", params=params,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(_run, run.id)
    return {"run_id": run.id, "status": "queued"}


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    rows = (
        db.query(models.AnalysisRun)
        .order_by(models.AnalysisRun.created_at.desc())
        .all()
    )
    return [_serialize(r) for r in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.AnalysisRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return _serialize(run, include_full=True)
