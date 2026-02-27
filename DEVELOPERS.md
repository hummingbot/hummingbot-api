# Hummingbot API Developer Guide

This guide explains how to develop and test hummingbot-api with custom branches of **hummingbot**, **gateway**, and **hummingbot-api**.

## Architecture Overview

The Hummingbot stack consists of three repositories:

| Repository | Purpose | Default Port |
|------------|---------|--------------|
| [hummingbot](https://github.com/hummingbot/hummingbot) | Core trading engine (Python) | N/A (library) |
| [gateway](https://github.com/hummingbot/gateway) | DEX connector service (TypeScript) | 15888 |
| [hummingbot-api](https://github.com/hummingbot/hummingbot-api) | REST API server (Python/FastAPI) | 8000 |

**Key insight**: hummingbot-api imports the `hummingbot` package as a library. When testing changes to hummingbot, you must either:
1. Build a wheel from your hummingbot branch and install it
2. Install hummingbot in editable mode

## Prerequisites

- **Conda**: Anaconda or Miniconda
- **Docker**: Docker Desktop (macOS/Windows) or Docker Engine (Linux)
- **Node.js 20+** and **pnpm** (for Gateway)
- **Git**

## Directory Structure (Recommended)

```
~/
├── hummingbot/           # Core trading engine
├── hummingbot-gateway/   # DEX connector service
└── hummingbot-api/       # REST API server
```

---

## Quick Start (Testing a PR)

If you're testing a PR that requires a custom hummingbot branch:

### Step 1: Clone and Build Hummingbot Wheel

```bash
# Clone hummingbot (if not already)
cd ~
git clone https://github.com/hummingbot/hummingbot.git
cd hummingbot

# Checkout the required branch/commit
git fetch origin
git checkout <branch-name>  # e.g., development, feat/lp-executor

# Create conda environment for building (uses Python 3.12)
conda env create -f setup/environment.yml
conda activate hummingbot

# Install build tools
pip install build wheel

# Build the wheel
python -m build --wheel --no-isolation

# The wheel will be in dist/
ls dist/*.whl
# Example output: hummingbot-20260126-cp312-cp312-linux_x86_64.whl
```

**Important**: The wheel must be built with Python 3.12 to match hummingbot-api's environment.

### Step 2: Update environment.yml

Edit `hummingbot-api/environment.yml` to use your local wheel:

```yaml
dependencies:
  # ... other dependencies ...
  - pip:
      - /path/to/hummingbot/dist/hummingbot-YYYYMMDD-cp312-cp312-<platform>.whl
      - msgpack>=1.0.5
      # ... other pip packages ...
```

**Platform suffixes:**
- Linux x86_64: `linux_x86_64`
- Linux ARM64: `linux_aarch64`
- macOS Intel: `macosx_10_9_x86_64`
- macOS Apple Silicon: `macosx_11_0_arm64`

### Step 3: Create/Update hummingbot-api Environment

```bash
cd ~/hummingbot-api

# If environment exists, remove it first
conda env remove -n hummingbot-api -y

# Create fresh environment with your wheel
conda env create -f environment.yml
conda activate hummingbot-api
```

### Step 4: Run the API

```bash
# Start infrastructure (postgres + EMQX)
docker compose up emqx postgres -d

# Run API in dev mode
make run
```

---

## Docker Development (Building Custom Images)

For testing with Docker containers, you need a **Linux wheel** (not macOS/Windows).

### Step 1: Build Linux Wheel for Docker

Build the wheel inside Docker to ensure Linux compatibility:

```bash
cd ~/hummingbot

# Build Linux wheel using Docker (Python 3.12)
docker run --rm -v ~/hummingbot:/hummingbot -w /hummingbot continuumio/miniconda3 bash -c "
  apt-get update -qq && apt-get install -y -qq gcc g++ build-essential > /dev/null 2>&1 &&
  conda create -n hummingbot-build python=3.12 cython numpy -y -q &&
  conda run -n hummingbot-build pip install -q build wheel &&
  conda run -n hummingbot-build python -m build --wheel
"

# Verify the Linux wheel was created
ls dist/*linux*.whl
# Example: hummingbot-20260126-cp312-cp312-linux_aarch64.whl
```

### Step 2: Build hummingbot-api Docker Image

```bash
# Copy the Linux wheel into the hummingbot-api directory
cp ~/hummingbot/dist/hummingbot-*-cp312-*-linux_*.whl ~/hummingbot-api/

# Update environment.docker.yml with the wheel filename
# Edit the pip section to match your wheel:
#   - ./hummingbot-YYYYMMDD-cp312-cp312-linux_aarch64.whl

# Build the Docker image
cd ~/hummingbot-api
docker build -t hummingbot/hummingbot-api:dev .

# Update docker-compose.yml to use your image
# Change: image: hummingbot/hummingbot-api:latest
# To:     image: hummingbot/hummingbot-api:dev

# Deploy
make deploy
```

### Verify Docker Deployment

```bash
# Check containers are running
docker ps | grep hummingbot

# Test the API
curl http://localhost:8000/
# Expected: {"name":"Hummingbot API","version":"...","status":"running"}

# Verify hummingbot is imported correctly
docker exec hummingbot-api python -c "import hummingbot; print(hummingbot.__file__)"
# Expected: /opt/conda/envs/hummingbot-api/lib/python3.12/site-packages/hummingbot/__init__.py
```

### Build All Components

For comprehensive testing, build all three components:

```bash
# 1. Build hummingbot wheel (see Step 1 above)

# 2. Build hummingbot Docker image
cd ~/hummingbot
docker build -t hummingbot/hummingbot:dev .

# 3. Build gateway Docker image
cd ~/hummingbot-gateway
docker build -t hummingbot/gateway:dev .

# 4. Build hummingbot-api Docker image (with custom wheel)
cd ~/hummingbot-api
docker build -t hummingbot/hummingbot-api:dev .
```

---

## Alternative: Editable Install (For Active Development)

If you're actively developing hummingbot alongside hummingbot-api:

```bash
# Create hummingbot-api environment WITHOUT the wheel reference
# Edit environment.yml and remove the hummingbot wheel line

# Create environment
conda env create -f environment.yml
conda activate hummingbot-api

# Install hummingbot in editable mode
cd ~/hummingbot
pip install -e .

# Verify it's using your local source
python -c "import hummingbot; print(hummingbot.__file__)"
# Should print: /Users/you/hummingbot/hummingbot/__init__.py (NOT site-packages)
```

With editable install, changes to hummingbot source are reflected immediately without reinstalling.

---

## Gateway Setup (For DEX Trading)

### Source Build

```bash
cd ~/hummingbot-gateway
git checkout development  # or your branch

# Install dependencies
pnpm install

# Build
pnpm build

# Create config directories
mkdir -p conf certs

# Run in development mode (HTTP, no TLS)
GATEWAY_PASSPHRASE=admin pnpm start --dev
```

### Docker

```bash
# Using pre-built image
docker run -d \
  --name gateway \
  -p 15888:15888 \
  -e GATEWAY_PASSPHRASE=admin \
  hummingbot/gateway:development

# Or build your own
cd ~/hummingbot-gateway
docker build -t hummingbot/gateway:dev .
docker run -d --name gateway -p 15888:15888 -e GATEWAY_PASSPHRASE=admin hummingbot/gateway:dev
```

---

## Full Development Stack

Run all components together for integration testing:

```bash
# Terminal 1: Infrastructure
cd ~/hummingbot-api
docker compose up emqx postgres -d

# Terminal 2: Gateway (if needed)
cd ~/hummingbot-gateway
GATEWAY_PASSPHRASE=admin pnpm start --dev

# Terminal 3: Hummingbot API
cd ~/hummingbot-api
conda activate hummingbot-api
uvicorn main:app --reload
```

Or use Docker for everything:

```bash
# Start Gateway separately (if using DEX features)
docker run -d --name gateway -p 15888:15888 -e GATEWAY_PASSPHRASE=admin hummingbot/gateway:development

# Start hummingbot-api stack
cd ~/hummingbot-api
make deploy
```

---

## Verifying Your Setup

### Check API is running

```bash
curl http://localhost:8000/
# Expected: {"name":"Hummingbot API","version":"...","status":"running"}
```

### Check if using local hummingbot

```bash
conda activate hummingbot-api
python -c "import hummingbot; print(hummingbot.__file__)"
```

- **Wheel install**: Shows `.../site-packages/hummingbot/__init__.py`
- **Editable install**: Shows `/path/to/hummingbot/hummingbot/__init__.py`

### Check Gateway connectivity

```bash
curl http://localhost:15888/
# Should return Gateway status
```

---

## Rebuilding After Changes

### After hummingbot changes

```bash
cd ~/hummingbot
conda activate hummingbot

# Rebuild wheel
python -m build --wheel --no-isolation

# Reinstall into hummingbot-api environment
/opt/anaconda3/envs/hummingbot-api/bin/pip install dist/hummingbot-*-cp312-*.whl --force-reinstall --no-deps

# Restart API
# (Ctrl+C on running API, then make run)
```

### After hummingbot-api changes

With `--reload` flag (default in `make run`), changes are picked up automatically.

### After gateway changes

```bash
cd ~/hummingbot-gateway
pnpm build
# Restart gateway process
```

---

## Common Issues & Troubleshooting

### "No module named 'hummingbot'"

**Cause**: The hummingbot wheel is not installed or path is incorrect.

**Fix**:
```bash
# Check if hummingbot is installed
conda activate hummingbot-api
pip show hummingbot

# If not, verify wheel path in environment.yml and reinstall
conda env remove -n hummingbot-api -y
conda env create -f environment.yml
```

### "Cannot find wheel file"

**Cause**: The wheel path in `environment.yml` doesn't exist or is platform-specific.

**Fix**: Use the correct wheel for your platform:
```bash
ls ~/hummingbot/dist/
# Use the wheel matching your Python version and platform
```

### Import errors after updating hummingbot

**Cause**: Old cached bytecode or stale installation.

**Fix**:
```bash
# Force reinstall
pip install ~/hummingbot/dist/hummingbot-*.whl --force-reinstall --no-deps

# Clear Python cache
find ~/hummingbot-api -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

### Gateway connection refused

**Cause**: Gateway not running or wrong port.

**Fix**:
```bash
# Check if Gateway is running
curl http://localhost:15888/

# Check GATEWAY_URL in .env
grep GATEWAY_URL .env

# Ensure Gateway is started with --dev flag for HTTP mode
```

### "solders" installation fails

**Cause**: `solders` is a pip-only package that conflicts with conda.

**Fix**: Remove solders from conda environment.yml (if present) and install via pip:
```bash
pip install solders>=0.19.0
```

### Docker build fails with wheel error

**Cause**: Wheel is built for different platform than Docker container (e.g., macOS wheel in Linux container).

**Fix**: Build the wheel inside Docker with Python 3.12:
```bash
# Build Linux wheel for Docker (Python 3.12 to match hummingbot-api)
docker run --rm -v ~/hummingbot:/hummingbot -w /hummingbot continuumio/miniconda3 bash -c "
  apt-get update -qq && apt-get install -y -qq gcc g++ build-essential > /dev/null 2>&1 &&
  conda create -n build python=3.12 cython numpy -y -q &&
  conda run -n build pip install -q build wheel &&
  conda run -n build python -m build --wheel
"
```

**Common platform wheel suffixes:**
- `linux_x86_64` - Linux AMD/Intel 64-bit
- `linux_aarch64` - Linux ARM64 (Apple Silicon Docker, AWS Graviton)
- `macosx_11_0_arm64` - macOS Apple Silicon (native, NOT for Docker)
- `macosx_10_9_x86_64` - macOS Intel (native, NOT for Docker)

---

## Branch Selection Reference

| Component | Default Branch | Description |
|-----------|---------------|-------------|
| hummingbot | `development` | Latest features, may be unstable |
| gateway | `development` | Latest features |
| hummingbot-api | `main` | Stable release |

For PRs, checkout the specific PR branch:
```bash
git fetch origin pull/<PR_NUMBER>/head:<LOCAL_BRANCH_NAME>
git checkout <LOCAL_BRANCH_NAME>
```

---

## Environment Variables

Key environment variables for development:

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_URL` | `http://localhost:15888` | Gateway service URL |
| `GATEWAY_PASSPHRASE` | `admin` | Gateway encryption passphrase |
| `DATABASE_URL` | (see .env) | PostgreSQL connection string |
| `BROKER_HOST` | `localhost` | EMQX MQTT broker host |
| `DEBUG_MODE` | `false` | Enable debug logging |

---

## Testing Integration

Run smoke tests to verify the stack:

```bash
# API status
curl http://localhost:8000/
# Expected: {"name":"Hummingbot API","version":"...","status":"running"}

# Check available executor types (includes lp_executor)
curl -u admin:admin http://localhost:8000/executors/types/available

# Gateway status (requires authentication)
curl -u admin:admin http://localhost:8000/gateway/status

# Gateway health (if running separately)
curl http://localhost:15888/

# Swagger UI for interactive testing
open http://localhost:8000/docs
```

---

## Summary Workflow

1. **Clone repos** to `~/hummingbot`, `~/hummingbot-gateway`, `~/hummingbot-api`
2. **Checkout branches** for each component you're testing
3. **Build hummingbot wheel** from your branch
4. **Update environment.yml** to reference your wheel
5. **Create/update conda environment** with `conda env create -f environment.yml`
6. **Start infrastructure** with `docker compose up emqx postgres -d`
7. **Start Gateway** if testing DEX features
8. **Run API** with `make run`
9. **Test** your changes
10. **Rebuild wheel** after hummingbot changes
