"""Shared arcade leaderboard for Goosebase games.

One table (`leaderboard_scores`) backs every game; the `game` slug namespaces
entries so a new game needs no backend change. Scores come from client-side
games, so they're trusted loosely: names are sanitized (alnum + profanity
block) and scores range-checked, but we don't try to defeat determined cheaters.

This router is mounted under the *public* prefix (settings.public_api_prefix,
default /public_api), so it sits outside the Cloudflare Access gate on /api/*.
Reads (GET) are open; writes (POST) require the game's posting key (see
config.GAME_POST_KEYS), which authorizes known games and blocks drive-by
writes. The key ships in client-side game code, so it is not a true secret.
"""

import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import LeaderboardScore

router = APIRouter(prefix="/games", tags=["games"])

MAX_ENTRIES = 10
NAME_LEN = 5
MAX_SCORE = 100_000_000          # sanity ceiling — rejects obvious garbage only
GAME_RE = re.compile(r"^[a-z0-9-]{1,32}$")

# Substrings that must not appear in a (de-leetspeoken) name. Kept short and
# uppercase; names are at most 5 alnum chars so this is deliberately blunt.
_BANNED = {
    "FUCK", "FUK", "FUC", "SHIT", "SHT", "CUNT", "KUNT", "DICK", "DIK",
    "COCK", "KOCK", "PUSY", "PUSSY", "TWAT", "WANK", "CUM", "FAG", "FAG0",
    "NIGR", "NIGA", "NIGE", "NGGR", "NGR", "SPIC", "KIKE", "CHNK", "CHINK",
    "GOOK", "RAPE", "NAZI", "HITLR", "KKK", "JIZZ", "SLUT", "HOE", "WHORE",
    "ANUS", "ARSE", "ASS", "BTCH", "BICH", "BTH",
}
# Leet → letter folding so "5H1T"/"4SS" are caught by the substring check.
_LEET = str.maketrans({"0": "O", "1": "I", "3": "E", "4": "A", "5": "S", "7": "T", "8": "B"})


def sanitize_name(raw: str) -> str:
    """Uppercase, strip to A-Z0-9, clamp length. Returns '' if nothing usable."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())[:NAME_LEN]


def is_clean(name: str) -> bool:
    folded = name.translate(_LEET)
    return not any(bad in folded for bad in _BANNED)


# ── Schemas (inline, matching landing.py style) ─────────────────────


class ScoreIn(BaseModel):
    game: str
    name: str
    score: int

    @field_validator("score")
    @classmethod
    def _score_range(cls, v: int) -> int:
        if v < 0 or v > MAX_SCORE:
            raise ValueError("score out of range")
        return v


class Entry(BaseModel):
    name: str
    score: int


class BoardOut(BaseModel):
    scores: list[Entry]


class SubmitOut(BaseModel):
    entry: Entry
    rank: int | None      # 1-based rank within the trimmed top list, or null if it didn't make it
    scores: list[Entry]


# ── Helpers ─────────────────────────────────────────────────────────


def _validate_game(game: str) -> str:
    g = (game or "").strip().lower()
    if not GAME_RE.match(g):
        raise HTTPException(status_code=400, detail="invalid game slug")
    return g


def _require_post_key(game: str, key: str | None) -> None:
    """Authorize a score write. The caller must present the posting key issued
    for this game (GAME_POST_KEYS). The key is bound to the slug, so a key for
    one game can't write to another's board. This is authorization for known
    games + drive-by-abuse defense, NOT anti-cheat: the key ships in
    client-side game code and is therefore not secret. Constant-time compare
    avoids leaking the key via response timing."""
    expected = settings.game_post_key_map.get(game)
    if not expected:
        # No key registered for this slug → the game isn't authorized to post.
        raise HTTPException(status_code=403, detail="game not authorized")
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="invalid game key")


def _top(db: Session, game: str, limit: int = MAX_ENTRIES) -> list[Entry]:
    rows = (
        db.query(LeaderboardScore)
        .filter(LeaderboardScore.game == game)
        .order_by(LeaderboardScore.score.desc(), LeaderboardScore.created_at.asc())
        .limit(limit)
        .all()
    )
    return [Entry(name=r.name, score=r.score) for r in rows]


# ── Routes ──────────────────────────────────────────────────────────


@router.get("/leaderboard", response_model=BoardOut)
def get_leaderboard(
    game: str = Query(...),
    limit: int = Query(MAX_ENTRIES, ge=1, le=100),
    db: Session = Depends(get_db),
):
    g = _validate_game(game)
    return BoardOut(scores=_top(db, g, limit))


@router.post("/leaderboard", response_model=SubmitOut)
def submit_score(
    body: ScoreIn,
    db: Session = Depends(get_db),
    x_game_key: str | None = Header(default=None, alias="X-Game-Key"),
):
    g = _validate_game(body.game)
    _require_post_key(g, x_game_key)

    name = sanitize_name(body.name) or "???"
    if name != "???" and not is_clean(name):
        raise HTTPException(status_code=400, detail="name not allowed")

    row = LeaderboardScore(game=g, name=name, score=body.score)
    db.add(row)
    db.commit()

    # Trim: keep only the top MAX_ENTRIES rows per game so the table can't grow
    # unbounded from a busy board. Cheap to do on write at this scale.
    keep_ids = [
        r.id for r in db.query(LeaderboardScore.id)
        .filter(LeaderboardScore.game == g)
        .order_by(LeaderboardScore.score.desc(), LeaderboardScore.created_at.asc())
        .limit(MAX_ENTRIES)
        .all()
    ]
    db.query(LeaderboardScore).filter(
        LeaderboardScore.game == g,
        LeaderboardScore.id.notin_(keep_ids),
    ).delete(synchronize_session=False)
    db.commit()

    scores = _top(db, g)
    made_it = row.id in keep_ids
    rank = next((i + 1 for i, r in enumerate(scores)
                 if r.name == name and r.score == body.score), None) if made_it else None
    return SubmitOut(entry=Entry(name=name, score=body.score), rank=rank, scores=scores)
