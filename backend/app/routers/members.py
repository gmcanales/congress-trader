from typing import List, Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/members", tags=["members"])


@router.get("/", response_model=List[schemas.MemberOut])
def list_members(
    state: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Member)
    if state:
        query = query.filter(models.Member.state_dst.startswith(state.upper()))
    if q:
        query = query.filter(
            models.Member.last_name.ilike(f"%{q}%")
            | models.Member.first_name.ilike(f"%{q}%")
        )
    return query.order_by(models.Member.last_name).all()
