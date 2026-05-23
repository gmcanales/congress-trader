"""Landing-page settings: active splash image + active color palette."""

import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.database import engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/landing", tags=["landing"])

SPLASH_KEY = "landing.splash"
PALETTE_KEY = "landing.palette"

DEFAULT_SPLASH = "goose-portrait-master.jpg"
DEFAULT_PALETTE = "warmMarsh"

# Keep in sync with :root[data-palette=...] selectors in tokens.css.
ALLOWED_PALETTES = {
    "warmMarsh",
    "sunset",
    "ember-blue",
    "forest-terminal",
    "retro-gold",
    "cyber-neon",
    "solarized-sand",
    "cleanmeter",
    "frankli",
    "nightSky",
}

_PALETTE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,31}$")


# ── Splash ──────────────────────────────────────────────────────────


class SplashIn(BaseModel):
    image: str


class SplashOut(BaseModel):
    image: str


@router.get("/splash", response_model=SplashOut)
def get_splash():
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT value->>'image' AS image FROM app_settings WHERE key = :k"),
            {"k": SPLASH_KEY},
        ).first()
    return SplashOut(image=row.image if row and row.image else DEFAULT_SPLASH)


@router.post("/splash", response_model=SplashOut)
def set_splash(body: SplashIn):
    name = body.image.strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="invalid image filename")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value, updated_at) "
                "VALUES (:k, CAST(:v AS JSONB), now()) "
                "ON CONFLICT (key) DO UPDATE "
                "SET value = EXCLUDED.value, updated_at = now()"
            ),
            {"k": SPLASH_KEY, "v": f'{{"image":"{name}"}}'},
        )
    return SplashOut(image=name)


# ── Palette ─────────────────────────────────────────────────────────


class PaletteIn(BaseModel):
    name: str


class PaletteOut(BaseModel):
    name: str


@router.get("/palette", response_model=PaletteOut)
def get_palette():
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT value->>'name' AS name FROM app_settings WHERE key = :k"),
            {"k": PALETTE_KEY},
        ).first()
    name = row.name if row and row.name else DEFAULT_PALETTE
    if name not in ALLOWED_PALETTES:
        name = DEFAULT_PALETTE
    return PaletteOut(name=name)


@router.post("/palette", response_model=PaletteOut)
def set_palette(body: PaletteIn):
    name = body.name.strip()
    if not _PALETTE_NAME_RE.match(name) or name not in ALLOWED_PALETTES:
        raise HTTPException(status_code=400, detail="unknown palette")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings (key, value, updated_at) "
                "VALUES (:k, CAST(:v AS JSONB), now()) "
                "ON CONFLICT (key) DO UPDATE "
                "SET value = EXCLUDED.value, updated_at = now()"
            ),
            {"k": PALETTE_KEY, "v": f'{{"name":"{name}"}}'},
        )
    return PaletteOut(name=name)
