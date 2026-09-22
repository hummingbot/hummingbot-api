"""The app-wide handler for request validation errors.

Lives outside main.py so tests can mount the handler the real app uses: a router tested
on a bare FastAPI() gets FastAPI's default handler instead, which is how a 422 that the
real app could not render went unnoticed.
"""

import logging

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for validation errors to log detailed error messages.
    """
    # Build a readable error message from validation errors
    error_messages = []
    for error in exc.errors():
        loc = " -> ".join(str(part) for part in error.get("loc", []))
        msg = error.get("msg", "Validation error")
        error_messages.append(f"{loc}: {msg}")

    # Log the validation error with details
    logging.warning(
        f"Validation error on {request.method} {request.url.path}: {'; '.join(error_messages)}"
    )

    # Return standard FastAPI validation error response. jsonable_encoder is what makes a
    # validator that raises ValueError renderable: pydantic puts the exception object
    # itself in the error's ctx, and JSONResponse cannot serialize it.
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )
