from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from function.errors import ConflictError, ForbiddenError, NotFoundError
from middleware.csrf import require_json
from routes import auth, drawings, health, workspaces
from utils.config import Settings, get_settings
from utils.db import create_client, ensure_indexes

ERROR_RESPONSES: dict[type[Exception], tuple[int, str]] = {
    NotFoundError: (status.HTTP_404_NOT_FOUND, "Not found"),
    ForbiddenError: (
        status.HTTP_403_FORBIDDEN,
        "You don't have permission to do that",
    ),
    ConflictError: (
        status.HTTP_409_CONFLICT,
        "Someone else is saving at the same moment, try again",
    ),
}


async def handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    status_code, detail = ERROR_RESPONSES[type(error)]
    return JSONResponse({"detail": detail}, status_code=status_code)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = create_client(settings)
        app.state.db = client[settings.mongo_db]
        await ensure_indexes(app.state.db)
        yield
        await client.close()

    app = FastAPI(title="Lukis API", lifespan=lifespan)
    app.state.settings = settings
    app.middleware("http")(require_json)
    for error_type in ERROR_RESPONSES:
        app.add_exception_handler(error_type, handle_domain_error)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(drawings.router)
    return app


app = create_app()
