.PHONY: setup run deploy stop install uninstall build install-pre-commit tailscale-status reset

SETUP_SENTINEL := .setup-complete

setup: $(SETUP_SENTINEL)

$(SETUP_SENTINEL):
	chmod +x setup.sh
	./setup.sh

# Run locally (dev mode)
# When TAILSCALE_ENABLED=true: installs Tailscale if needed, connects, configures tailscale serve,
# then binds uvicorn to 127.0.0.1 only (tailscale serve exposes port 8000 on the tailnet)
run:
	docker compose up emqx postgres -d
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if [ "$${TAILSCALE_ENABLED:-false}" = "true" ]; then \
		echo "[INFO] Tailscale mode: setting up Tailscale for source install..."; \
		if ! command -v tailscale >/dev/null 2>&1; then \
			echo "[INFO] Installing Tailscale..."; \
			curl -fsSL https://tailscale.com/install.sh | sh; \
		fi; \
		if ! tailscale status >/dev/null 2>&1; then \
			echo "[INFO] Connecting to Tailscale network..."; \
			sudo tailscale up --authkey="$${TAILSCALE_AUTH_KEY}" --hostname="$${TAILSCALE_HOSTNAME:-hummingbot-api}" --accept-dns=true; \
		fi; \
		tailscale serve status 2>/dev/null | grep -q ":8000" || \
			sudo tailscale serve --bg http:8000 http://localhost:8000; \
		echo "[INFO] Binding uvicorn to 127.0.0.1 (tailscale serve exposes port 8000 on tailnet)"; \
		conda run --no-capture-output -n hummingbot-api uvicorn main:app --reload --host 127.0.0.1 --port 8000; \
	else \
		conda run --no-capture-output -n hummingbot-api uvicorn main:app --reload; \
	fi

# Deploy with Docker
# When TAILSCALE_ENABLED=true: adds the Tailscale sidecar compose override
deploy: $(SETUP_SENTINEL)
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if [ "$${TAILSCALE_ENABLED:-false}" = "true" ]; then \
		echo "[INFO] Deploying with Tailscale sidecar..."; \
		docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d; \
	else \
		docker compose up -d; \
	fi

TAILSCALE_CONTAINER := hummingbot-tailscale

# Show Tailscale connection status (Docker sidecar or local install)
tailscale-status:
	@if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx '$(TAILSCALE_CONTAINER)'; then \
		echo "[INFO] Tailscale sidecar (Docker)"; \
		docker exec $(TAILSCALE_CONTAINER) tailscale status; \
	elif command -v tailscale >/dev/null 2>&1; then \
		echo "[INFO] Tailscale (local)"; \
		tailscale status; \
	else \
		echo "Tailscale is not available."; \
		echo "  Docker deploy: ensure TAILSCALE_ENABLED=true and run 'make deploy'"; \
		echo "  Source run:    use 'make run' with Tailscale enabled (installs locally)"; \
		exit 1; \
	fi

# Stop all services
stop:
	docker compose down

# Install conda environment
install:
	@if ! command -v conda >/dev/null 2>&1; then \
		echo "Error: Conda is not found in PATH. Please install Conda or add it to your PATH."; \
		exit 1; \
	fi
	@if conda env list | grep -q '^hummingbot-api '; then \
		echo "Environment already exists."; \
	else \
		conda env create -f environment.yml; \
	fi
	$(MAKE) install-pre-commit
	$(MAKE) setup

uninstall:
	conda env remove -n hummingbot-api -y
	rm -f $(SETUP_SENTINEL)

install-pre-commit:
	conda run -n hummingbot-api pip install pre-commit
	conda run -n hummingbot-api pre-commit install

# Build Docker image
build:
	docker build -t hummingbot/hummingbot-api:latest .

# Reset to near-origin state:
#   - stops Docker containers (with volume wipe) and/or source uvicorn if running
#   - removes .env and .setup-complete from the project root
#   - removes all credential folders under bots/credentials/ except master_account
#   - removes all .yml files under bots/credentials/master_account/
reset:
	@echo "[INFO] Checking for running hummingbot-api services..."
	@if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'hummingbot-api'; then \
		echo "[INFO] Docker containers running — stopping and wiping volumes..."; \
		docker compose down -v; \
	else \
		echo "[INFO] No Docker containers running."; \
	fi
	@if pgrep -f "uvicorn main:app" >/dev/null 2>&1; then \
		echo "[INFO] Source uvicorn process found — stopping..."; \
		pkill -f "uvicorn main:app" || true; \
	fi
	@echo "[INFO] Removing .env and .setup-complete..."
	rm -f .env $(SETUP_SENTINEL)
	@echo "[INFO] Clearing credentials..."
	@find bots/credentials -mindepth 1 -maxdepth 1 -type d ! -name master_account -exec rm -rf {} +
	@find bots/credentials/master_account -name "*.yml" -delete
	@rm -f bots/credentials/master_account/.password_verification
	@echo "[INFO] Reset complete."
