import jsonschema_rs
import structlog
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from langgraph_api.js.errors import RemoteException

logger = structlog.stdlib.get_logger(__name__)


def is_body_allowed_for_status_code(status_code: int | None) -> bool:
    if status_code is None:
        return True
    return not (status_code < 200 or status_code in {204, 205, 304})


async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    headers = getattr(exc, "headers", None)
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)

    detail = exc.detail
    if not detail or not isinstance(detail, str):
        logger.warning(
            "HTTPException detail is not a string or was not set",
            detail_type=type(detail).__name__,
            status_code=exc.status_code,
        )
        # Use safe fallback that won't fail or leak sensitive info
        detail = "unknown error"

    return JSONResponse(
        {"detail": detail}, status_code=exc.status_code, headers=headers
    )


async def validation_error_handler(request, exc: jsonschema_rs.ValidationError):
    request.scope["error_detail"] = str(exc)
    return await http_exception_handler(
        request, HTTPException(status_code=422, detail=str(exc))
    )


async def value_error_handler(request, exc: ValueError):
    logger.exception("Bad Request Error", exc_info=exc)
    return await http_exception_handler(
        request, HTTPException(status_code=400, detail=str(exc))
    )


async def remote_exception_handler(request, exc: RemoteException):
    logger.exception("Remote Exception", exc_info=exc)
    return await http_exception_handler(
        request, HTTPException(status_code=500, detail=str(exc))
    )


async def overloaded_error_handler(request, exc: ValueError):
    logger.exception("Overloaded Error", exc_info=exc)
    return await http_exception_handler(
        request, HTTPException(status_code=503, detail=str(exc))
    )


class UserInterrupt(Exception):
    def __init__(self, message="User interrupted the run"):
        super().__init__(message)


class UserRollback(UserInterrupt):
    def __init__(self):
        super().__init__("User requested rollback of the run")


class UserTimeout(Exception):
    def __init__(self, timeout_error: TimeoutError):
        super().__init__(timeout_error)
        self.timeout_error = timeout_error
