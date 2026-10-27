from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "lukis"
    # Turn on in production, where the app is served over HTTPS.
    cookie_secure: bool = False
    session_days: int = 30
    # The site's origin(s), comma-separated. Live-collaboration WebSockets
    # opened from any other origin are refused.
    frontend_origin: str = "http://localhost:3000"
    # S3-compatible storage (RustFS) for images and thumbnails. The browser
    # never talks to it; files go through the API.
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "lukis"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    # Largest image accepted, after the editor has downscaled it. Matches the
    # editor's own limit.
    max_file_bytes: int = 4 * 1024 * 1024

    @property
    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]
