import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from function.errors import DomainError, TooManyRequestsError
from function.rate_limit import RateLimiter
from function.realtime import Hub
from middleware.csrf import require_json
from routes import auth, drawings, files, health, invites, live, workspaces
from utils.config import Settings, get_settings
from utils.db import create_client, ensure_indexes
from utils.logs import log_requests, show_app_logs
from utils.mail import Mailer, create_mailer
from utils.storage import S3Storage, Storage, StorageError

logger = logging.getLogger("lukis")


async def handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
    headers = None
    if isinstance(error, TooManyRequestsError):
        headers = {"Retry-After": str(error.retry_after)}
    return JSONResponse(
        {"detail": error.detail}, status_code=error.status_code, headers=headers
    )


async def handle_storage_error(request: Request, error: StorageError) -> JSONResponse:
    logger.warning(
        f"Object storage failed: {error}", extra={"fields": {"path": request.url.path}}
    )
    return JSONResponse(
        {"detail": "File storage is unavailable, please try again"}, status_code=503
    )


def create_app(
    settings: Settings | None = None,
    storage: Storage | None = None,
    mailer: Mailer | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    show_app_logs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = create_client(settings)
        app.state.db = client[settings.mongo_db]
        await ensure_indexes(app.state.db)
        yield
        await client.close()

    app = FastAPI(title="Lukis API", lifespan=lifespan)
    app.state.settings = settings
    # Images and thumbnails. Creating the client doesn't connect yet.
    app.state.storage = storage or S3Storage(settings)
    app.state.mailer = mailer or create_mailer(settings)
    # Who's connected to which drawing, for live collaboration.
    app.state.live = Hub()
    # Attempt counts for logins, sign-ups and emails.
    app.state.rate_limiter = RateLimiter()
    app.middleware("http")(require_json)
    # Added last, so it runs first and also logs requests refused above.
    app.middleware("http")(log_requests)
    # Covers every subclass: NotFoundError, ForbiddenError, ConflictError…
    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(StorageError, handle_storage_error)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(drawings.router)
    app.include_router(files.router)
    app.include_router(invites.router)
    app.include_router(live.router)
    return app


app = create_app()
