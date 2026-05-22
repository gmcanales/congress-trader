import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base, SessionLocal
from app import models
from app.routers import (
    members, transactions, prices, scraper, model, datasets,
    recommendations, holdings, assets,
)
from app.services import snapshot as snapshot_service

logging.basicConfig(level=logging.INFO)

SQL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql")
MIGRATIONS_SQL = os.path.join(SQL_DIR, "migrations.sql")
VIEWS_SQL = os.path.join(SQL_DIR, "views.sql")


def _apply_sql_file(path: str, label: str) -> None:
    """Run a SQL file in one transaction. Every statement must be idempotent."""
    try:
        with open(path) as f:
            sql = f.read()
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(sql)
            raw.commit()
        finally:
            raw.close()
        logging.info(f"{label} applied")
    except Exception as exc:
        logging.warning(f"Could not apply {label}: {exc}")


ET = ZoneInfo("America/New_York")
SNAPSHOT_HOUR_ET = int(os.getenv("SNAPSHOT_HOUR_ET", "6"))   # 6am ET default
SNAPSHOT_MINUTE_ET = int(os.getenv("SNAPSHOT_MINUTE_ET", "0"))


def _today_snapshot_exists() -> bool:
    """Cheap check used at startup so we only backfill when we have to."""
    db = SessionLocal()
    try:
        return db.query(
            db.query(models.RecommendationSnapshot)
            .filter(models.RecommendationSnapshot.as_of == date.today())
            .exists()
        ).scalar()
    except Exception as exc:
        logging.warning(f"snapshot-existence check failed: {exc}")
        return True   # don't trigger a backfill if we can't tell
    finally:
        db.close()


def _refresh_snapshots_sync():
    """Sync wrapper for the snapshot service so we can offload to a worker
    thread from the asyncio event loop."""
    try:
        snapshot_service.refresh_all_profiles()
    except Exception:
        logging.exception("daily snapshot refresh failed")


async def _scheduled_refresh():
    """APScheduler entry point. Runs the (synchronous, DB-heavy) refresh
    in a worker thread so it doesn't block the event loop."""
    logging.info("daily 6am-ET snapshot refresh starting")
    await asyncio.get_running_loop().run_in_executor(None, _refresh_snapshots_sync)
    logging.info("daily 6am-ET snapshot refresh done")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _apply_sql_file(MIGRATIONS_SQL, "migrations")
    _apply_sql_file(VIEWS_SQL, "views")

    scheduler = AsyncIOScheduler(timezone=ET)
    scheduler.add_job(
        _scheduled_refresh,
        trigger=CronTrigger(hour=SNAPSHOT_HOUR_ET,
                            minute=SNAPSHOT_MINUTE_ET,
                            timezone=ET),
        id="daily_recs_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,   # if container started late, still run within the hour
    )
    scheduler.start()
    logging.info(
        f"scheduler started: daily recs refresh at "
        f"{SNAPSHOT_HOUR_ET:02d}:{SNAPSHOT_MINUTE_ET:02d} ET"
    )

    # Backfill today's snapshot in the background if it's missing so the
    # first user doesn't pay the live-compute cost. Fire and forget.
    if not _today_snapshot_exists():
        logging.info("no snapshot for today found — scheduling startup backfill")
        asyncio.create_task(_scheduled_refresh())

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Congress Trader API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

prefix = settings.api_prefix
app.include_router(members.router, prefix=prefix)
app.include_router(transactions.router, prefix=prefix)
app.include_router(prices.router, prefix=prefix)
app.include_router(scraper.router, prefix=prefix)
app.include_router(model.router, prefix=prefix)
app.include_router(datasets.router, prefix=prefix)
app.include_router(recommendations.router, prefix=prefix)
app.include_router(holdings.router, prefix=prefix)
app.include_router(assets.reports_router, prefix=prefix)
app.include_router(assets.docs_router, prefix=prefix)


@app.get("/health")
def health():
    return {"status": "ok"}
