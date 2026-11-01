from functools import lru_cache
from typing import Annotated, Literal

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
    # Outgoing email. Optional: with no host, email features are switched off.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # "starttls" (usually port 587), "ssl" (usually 465) or "none".
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    mail_from: str = "Lukis <noreply@localhost>"
    # Version history: while a drawing is being edited, its scene is kept
    # about this often, and these automatic snapshots are kept this long.
    # Named versions stay until someone deletes them.
    snapshot_interval_minutes: int = 10
    version_retention_days: int = 30
    # Database backups (scripts/backup.py) go to this bucket, under backups/.
    # Defaults to s3_bucket.
    backup_bucket: str = ""
    backup_keep_days: int = 14
    # The code the first account needs to register and become site owner.
    # Without it a random one is printed in the logs at startup.
    setup_code: str = ""
    # Logging in with Google, GitHub or Microsoft. Each is offered once its
    # client ID and secret are set. Register
    # <first FRONTEND_ORIGIN>/api/auth/oauth/<provider>/callback with the
    # provider as the redirect URI.
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    # Which Microsoft accounts may log in: "common" (personal, work and
    # school), "organizations" (work and school), "consumers" (personal), or
    # one organization's tenant ID.
    microsoft_tenant: str = "common"

    @property
    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    @property
    def public_url(self) -> str:
        """Where links in emails point: the first frontend origin."""
        return self.frontend_origins[0].rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]
