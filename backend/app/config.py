from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://congress:congress@localhost:5432/congress_trader"
    api_prefix: str = "/api"

    # Cloudflare Access — see app/auth.py
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""
    cf_access_dev_user: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
