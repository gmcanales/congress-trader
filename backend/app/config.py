from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://congress:congress@localhost:5432/congress_trader"
    api_prefix: str = "/api"
    # Unauthenticated surface. Deliberately NOT under api_prefix so the
    # Cloudflare Access policy on /api/* doesn't gate it. Only routes that are
    # safe to expose publicly (the arcade leaderboard) are mounted here.
    public_api_prefix: str = "/public_api"

    # Per-game posting keys for the public leaderboard. Comma-separated
    # "slug:key" pairs, e.g. GAME_POST_KEYS=gooooose:abc123,other:def456.
    # The key ships inside the (client-side) game, so it is NOT a true secret:
    # it authorizes *known* games and blocks drive-by writes, but is not
    # anti-cheat. A game can only POST to the slug its key is bound to.
    game_post_keys: str = ""

    # Cloudflare Access — see app/auth.py
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""
    cf_access_dev_user: str = ""
    # Comma-separated "email:DisplayName" pairs, e.g.
    #   CF_USER_DISPLAY_NAMES=alice@example.com:Alice,bob@example.com:Bob
    cf_user_display_names: str = ""

    class Config:
        env_file = ".env"

    @property
    def game_post_key_map(self) -> dict[str, str]:
        """Parse GAME_POST_KEYS into {slug: key}. Malformed pairs are skipped."""
        out: dict[str, str] = {}
        for pair in self.game_post_keys.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            slug, key = pair.split(":", 1)
            slug, key = slug.strip().lower(), key.strip()
            if slug and key:
                out[slug] = key
        return out


settings = Settings()
