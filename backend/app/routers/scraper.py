from fastapi import APIRouter, BackgroundTasks, HTTPException

from app import schemas
from app.services.scraper import scrape_year
from app.services.senate_scraper import scrape_senate
from app.services.ingest import run_daily_ingest, get_last_ingest

router = APIRouter(prefix="/scrape", tags=["scraper"])


@router.post("/", response_model=schemas.ScrapeStatus)
def trigger_scrape(req: schemas.ScrapeRequest, background_tasks: BackgroundTasks):
    chamber = req.chamber.lower()
    if chamber == "house":
        background_tasks.add_task(scrape_year, req.year, req.max_docs)
    elif chamber == "senate":
        background_tasks.add_task(scrape_senate, req.year, req.max_docs)
    else:
        raise HTTPException(status_code=400, detail="chamber must be 'house' or 'senate'")
    return {"status": "queued", "year": req.year, "chamber": chamber}


@router.post("/daily")
def trigger_daily_ingest(background_tasks: BackgroundTasks):
    """Run the same pass the 4am-ET scheduler runs — both chambers, the
    relevant year(s) — without waiting for tomorrow. Returns immediately;
    poll GET /api/scrape/status for the outcome."""
    background_tasks.add_task(run_daily_ingest)
    return {"status": "queued"}


@router.get("/status")
def ingest_status():
    """Summary of the most recent daily ingest, or nulls if it has never
    run. Also surfaced on the Recommendations freshness banner."""
    last = get_last_ingest()
    return {"last_run": last}
