from fastapi import APIRouter, BackgroundTasks, HTTPException

from app import schemas
from app.services.scraper import scrape_year
from app.services.senate_scraper import scrape_senate

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
