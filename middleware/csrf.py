from fastapi import Request, status
from fastapi.responses import JSONResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def require_json(request: Request, call_next):
    """Rejects state-changing requests that aren't JSON.

    A cross-site page can only send JSON after a CORS preflight, which this API
    never approves, so this stops CSRF from plain HTML forms, including forms
    that log a victim into an attacker's account.
    """
    if request.method not in SAFE_METHODS:
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            return JSONResponse(
                {"detail": "Content-Type must be application/json"},
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
    return await call_next(request)
