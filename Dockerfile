# Stage 1: Builder stage
FROM python:3.12-slim-bookworm AS builder

# Install build dependencies (a safety net for any dependency without a prebuilt wheel
# for this interpreter/arch; the PyPI `ta-lib`/`TA-Lib` wheel bundles the TA-Lib C library
# for Linux, so no separate system libta-lib package is needed here).
RUN apt-get update && \
    apt-get install -y python3-dev gcc g++ build-essential && \
    rm -rf /var/lib/apt/lists/*

# Pinned for reproducible builds (the version uv.lock was written with); bump deliberately.
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /usr/local/bin/uv

# Use the image's own system python, not one uv would download: a downloaded standalone
# interpreter lives outside what the release stage copies below, which would leave
# .venv/bin/python a broken symlink once this builder stage is discarded.
ENV UV_PYTHON_PREFERENCE=only-system
# Precompile .pyc files at install time for faster container start-up.
ENV UV_COMPILE_BYTECODE=1

# Matches the runtime stage's WORKDIR below: the venv's console-script shebangs embed
# this absolute path, so builder and runtime must agree on it for the copied scripts
# (uvicorn, etc.) to resolve their interpreter after the copy.
WORKDIR /hummingbot-api

# Copy only the dependency manifests first (for better layer caching)
COPY pyproject.toml uv.lock .python-version .
RUN uv sync --locked --no-dev

# Stage 2: Runtime stage
FROM python:3.12-slim-bookworm

# Install only runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy the uv-managed venv from builder
COPY --from=builder /hummingbot-api/.venv /hummingbot-api/.venv

# Set the working directory
WORKDIR /hummingbot-api

# Copy only necessary application files
COPY main.py version.py config.py deps.py ./
COPY models ./models
COPY routers ./routers
COPY services ./services
COPY utils ./utils
COPY database ./database
COPY bots/controllers ./bots/controllers
COPY bots/scripts ./bots/scripts

# Create necessary directories
RUN mkdir -p bots/instances bots/conf bots/credentials bots/data bots/archived

# Expose port
EXPOSE 8000

# Put the uv-managed venv on PATH so non-login shells (e.g. `docker exec`) find its
# python + console scripts without activating it.
ENV PATH="/hummingbot-api/.venv/bin:$PATH"

# Run the application
ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
