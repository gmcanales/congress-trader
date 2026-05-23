"""Cloudflare Access JWT verification.

Every authenticated request through CF Access carries a signed JWT in the
`Cf-Access-Jwt-Assertion` header. We verify it against the team's public
keys (JWKS) and extract the caller's email from the claims. This is the
only identity source for per-user data scoping — header trust is not
accepted, because the backend port is LAN-reachable and a forged header
would otherwise impersonate any user.

Configuration (all read from .env via Settings):
  CF_ACCESS_TEAM_DOMAIN   e.g. YOUR-TEAM.cloudflareaccess.com
  CF_ACCESS_AUD           the Access Application's AUD tag (Zero Trust
                          dashboard → Access → Applications → Overview)
  CF_ACCESS_DEV_USER      optional. If set AND no JWT is present, the
                          dependency returns this email. Used only when
                          running outside CF Tunnel (e.g. dev_preview.py).
                          NEVER set in production.
"""
from __future__ import annotations

import logging

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not settings.cf_access_team_domain:
            raise RuntimeError("CF_ACCESS_TEAM_DOMAIN is not configured")
        url = f"https://{settings.cf_access_team_domain}/cdn-cgi/access/certs"
        _jwks_client = PyJWKClient(url, cache_keys=True, max_cached_keys=8)
    return _jwks_client


def current_user_email(request: Request) -> str:
    """FastAPI dependency: returns the verified caller's email.
    Raises 401 if no valid JWT is present."""
    token = request.headers.get("Cf-Access-Jwt-Assertion")

    if not token and settings.cf_access_dev_user:
        return settings.cf_access_dev_user.lower()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Cloudflare Access JWT",
        )

    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        # Refuse rather than silently skip the audience check.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudflare Access is not configured on this server",
        )

    try:
        key = _get_jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.cf_access_aud,
            issuer=f"https://{settings.cf_access_team_domain}",
        )
    except jwt.PyJWTError as exc:
        logger.warning("CF Access JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Cloudflare Access JWT",
        )

    email = claims.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT has no email claim",
        )
    return email.lower()
