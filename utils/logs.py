"""Structured logs: one JSON object per line on stdout, for `docker logs` and
any log collector. Turned on with `uvicorn --log-config logging.json`; without
it (local development) uvicorn's readable defaults are used.

Extra fields go in `extra={"fields": {...}}` on any logging call.
"""

import json
import logging
import time
from datetime import UTC, datetime

from fastapi import Request

request_logger = logging.getLogger("lukis.requests")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def show_app_logs() -> None:
    """Without --log-config only uvicorn's own loggers print anything, so give
    ours (lukis.*) a plain handler too. A no-op once logging is configured."""
    app_logger = logging.getLogger("lukis")
    if logging.getLogger().handlers or app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    # uvicorn's access log already shows each request here.
    request_logger.setLevel(logging.WARNING)


async def log_requests(request: Request, call_next):
    """Logs every request with its outcome and duration. Unhandled errors are
    logged with their traceback by uvicorn."""
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Health checks run every few seconds; only failures are interesting.
        if request.url.path != "/health" or status != 200:
            request_logger.log(
                logging.WARNING if status >= 500 else logging.INFO,
                f"{request.method} {request.url.path} {status}",
                extra={
                    "fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "client": request.client.host if request.client else None,
                    }
                },
            )
