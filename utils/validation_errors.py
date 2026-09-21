"""The response for a request that failed validation.

FastAPI's default handler runs jsonable_encoder over the error list before serialising it. A
validator that raised ValueError - a config name with a '.' in it, for one - leaves the
exception object itself in each error's ctx["error"], which JSONResponse cannot serialise; a
handler that returned the raw list turned every such 422 into a 500 with a text/plain body,
and the message the validator wrote never reached the client (dashboard#281).
"""
import logging

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log the failed fields readably and return the standard 422 body."""
    error_messages = []
    for error in exc.errors():
        loc = " -> ".join(str(part) for part in error.get("loc", []))
        msg = error.get("msg", "Validation error")
        error_messages.append(f"{loc}: {msg}")

    logging.warning(
        f"Validation error on {request.method} {request.url.path}: {'; '.join(error_messages)}"
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())},
    )
