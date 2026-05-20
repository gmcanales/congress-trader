import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.routers import members, transactions, prices, scraper, model, datasets

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _apply_sql_file(MIGRATIONS_SQL, "migrations")
    _apply_sql_file(VIEWS_SQL, "views")
    yield


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


@app.get("/health")
def health():
    return {"status": "ok"}
