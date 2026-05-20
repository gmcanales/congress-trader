import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.routers import members, transactions, prices, scraper, model, datasets

logging.basicConfig(level=logging.INFO)

VIEWS_SQL = os.path.join(os.path.dirname(__file__), "..", "sql", "views.sql")


def _apply_views():
    """Apply the analysis views (idempotent CREATE OR REPLACE)."""
    try:
        with open(VIEWS_SQL) as f:
            sql = f.read()
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(sql)
            raw.commit()
        finally:
            raw.close()
        logging.info("Analysis views applied")
    except Exception as exc:
        logging.warning(f"Could not apply analysis views: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _apply_views()
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
