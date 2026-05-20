from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://congress:congress@localhost:5432/congress_trader"
    api_prefix: str = "/api"

    class Config:
        env_file = ".env"


settings = Settings()
