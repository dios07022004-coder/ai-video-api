"""Application error taxonomy + FastAPI exception handlers.

Every deliberate failure raises an ``AppError`` subclass carrying a stable
``ErrorCode``, an HTTP status and safe ``details``. A single handler renders the
uniform envelope. Unhandled exceptions become a generic INTERNAL_ERROR — the raw
Python exception is logged, never returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.constants import ErrorCode
from app.logging import get_logger

logger = get_logger("api.errors")


class AppError(Exception):
    """Base class for all deliberate, client-safe errors."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "Internal error."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": {"code": self.code.value, "message": self.message, "details": self.details},
        }


# ── Concrete errors ──────────────────────────────────────────────────────────
class UnauthorizedError(AppError):
    code = ErrorCode.UNAUTHORIZED
    http_status = status.HTTP_401_UNAUTHORIZED
    message = "Missing or invalid API key."


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN
    http_status = status.HTTP_403_FORBIDDEN
    message = "Not permitted."


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Rate limit exceeded."


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND
    message = "Resource not found."


class TaskNotFoundError(NotFoundError):
    code = ErrorCode.TASK_NOT_FOUND
    message = "Task not found."


class ModeNotFoundError(NotFoundError):
    code = ErrorCode.MODE_NOT_FOUND
    message = "Mode not found."


class ModeDisabledError(AppError):
    code = ErrorCode.MODE_DISABLED
    http_status = status.HTTP_409_CONFLICT
    message = "Mode is disabled."


class WorkflowNotFoundError(NotFoundError):
    code = ErrorCode.WORKFLOW_NOT_FOUND
    message = "Workflow not found."


class ModelNotFoundError(NotFoundError):
    code = ErrorCode.MODEL_NOT_FOUND
    message = "Model not found."


class InvalidWorkflowError(AppError):
    code = ErrorCode.INVALID_WORKFLOW
    http_status = status.HTTP_400_BAD_REQUEST
    message = "Workflow JSON is invalid."


class PlaceholderUnresolvedError(AppError):
    code = ErrorCode.PLACEHOLDER_UNRESOLVED
    http_status = status.HTTP_400_BAD_REQUEST
    message = "Workflow contains unresolved placeholders."


class UploadInvalidError(AppError):
    code = ErrorCode.UPLOAD_INVALID
    http_status = status.HTTP_400_BAD_REQUEST
    message = "Uploaded file is invalid."


class UploadTooLargeError(AppError):
    code = ErrorCode.UPLOAD_TOO_LARGE
    http_status = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    message = "Uploaded file is too large."


class UnsupportedMediaTypeError(AppError):
    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    http_status = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    message = "Unsupported media type."


class AgeVerificationError(AppError):
    code = ErrorCode.AGE_VERIFICATION_FAILED
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    message = "The person in the image appears to be under the minimum age."


class ParamInvalidError(AppError):
    code = ErrorCode.PARAM_INVALID
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    message = "Invalid parameter."


class InsufficientCreditsError(AppError):
    code = ErrorCode.INSUFFICIENT_CREDITS
    http_status = status.HTTP_402_PAYMENT_REQUIRED
    message = "Insufficient credits."


class ComfyUnavailableError(AppError):
    code = ErrorCode.COMFY_UNAVAILABLE
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "ComfyUI backend is unavailable."


class ComfyExecutionError(AppError):
    code = ErrorCode.COMFY_EXECUTION_FAILED
    http_status = status.HTTP_502_BAD_GATEWAY
    message = "ComfyUI execution failed."


class GenerationTimeoutError(AppError):
    code = ErrorCode.GENERATION_TIMEOUT
    http_status = status.HTTP_504_GATEWAY_TIMEOUT
    message = "Generation timed out."


class StorageError(AppError):
    code = ErrorCode.STORAGE_ERROR
    message = "Storage operation failed."


class ConflictError(AppError):
    code = ErrorCode.CONFLICT
    http_status = status.HTTP_409_CONFLICT
    message = "Conflict."


# ── Handlers ─────────────────────────────────────────────────────────────────
def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        if exc.http_status >= 500:
            logger.error("app_error", code=exc.code.value, message=exc.message, path=request.url.path)
        return JSONResponse(status_code=exc.http_status, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Keep FastAPI's canonical 422 body for client tooling compatibility.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _status_to_code(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {"code": code.value, "message": str(exc.detail), "details": {}},
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path)
        body = {
            "success": False,
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "An unexpected error occurred.",
                "details": {},
            },
        }
        return JSONResponse(status_code=500, content=body)


def _status_to_code(http_status: int) -> ErrorCode:
    return {
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        413: ErrorCode.UPLOAD_TOO_LARGE,
        415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        429: ErrorCode.RATE_LIMITED,
    }.get(http_status, ErrorCode.INTERNAL_ERROR)
