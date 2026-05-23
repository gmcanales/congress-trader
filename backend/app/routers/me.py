"""Identity endpoint — returns the caller's verified email and display name."""
from fastapi import APIRouter, Depends

from app.auth import current_user_email
from app.config import settings

router = APIRouter(tags=["me"])


def _resolve_name(email: str) -> str:
    """Look up display name from CF_USER_DISPLAY_NAMES config.
    Falls back to the local-part of the email if not mapped."""
    raw = settings.cf_user_display_names.strip()
    if raw:
        for pair in raw.split(","):
            if ":" in pair:
                addr, name = pair.split(":", 1)
                if addr.strip().lower() == email.lower():
                    return name.strip()
    return email.split("@")[0]


@router.get("/me")
def get_me(email: str = Depends(current_user_email)):
    return {"email": email, "name": _resolve_name(email)}
