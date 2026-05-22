"""Read-only listing + serving of generated reports and project docs.

  GET /api/reports/         → [{ name, size, mtime }, ...]  (HTML report files)
  GET /api/reports/{name}   → the HTML file
  GET /api/docs/            → [{ name, size, mtime }, ...]  (markdown docs)
  GET /api/docs/{name}      → the markdown source (text/markdown)

Both directories are mounted into the container by docker-compose
(`./reports:/reports:ro`, `./docs:/docs:ro`). `name` is restricted to a
single path component to prevent traversal; no subdirectories are
exposed.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "/reports"))
DOCS_DIR = Path(os.getenv("DOCS_DIR", "/docs"))


reports_router = APIRouter(prefix="/reports", tags=["reports"])
docs_router = APIRouter(prefix="/docs", tags=["docs"])


def _safe_resolve(base: Path, name: str) -> Path:
    """Resolve `name` to a path inside `base`, blocking traversal."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid name")
    p = (base / name).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes base directory")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return p


def _list_dir(base: Path, exts: Iterable[str]) -> list[dict]:
    if not base.exists():
        return []
    exts_lower = {e.lower() for e in exts}
    rows = []
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in exts_lower:
            continue
        st = entry.stat()
        rows.append({
            "name": entry.name,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


# ── reports ────────────────────────────────────────────────────────────────

@reports_router.get("/")
def list_reports():
    return {
        "dir": str(REPORTS_DIR),
        "exists": REPORTS_DIR.exists(),
        "items": _list_dir(REPORTS_DIR, [".html", ".htm"]),
    }


@reports_router.get("/{name}")
def get_report(name: str):
    p = _safe_resolve(REPORTS_DIR, name)
    return FileResponse(p, media_type="text/html")


# ── docs ───────────────────────────────────────────────────────────────────

@docs_router.get("/")
def list_docs():
    return {
        "dir": str(DOCS_DIR),
        "exists": DOCS_DIR.exists(),
        "items": _list_dir(DOCS_DIR, [".md", ".markdown", ".txt"]),
    }


@docs_router.get("/{name}")
def get_doc(name: str):
    p = _safe_resolve(DOCS_DIR, name)
    # text/markdown so the browser shows it as text rather than offering download
    return FileResponse(p, media_type="text/markdown; charset=utf-8")
