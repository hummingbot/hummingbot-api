.PHONY: setup run deploy stop install uninstall build install-pre-commit tailscale-status

SETUP_SENTINEL := .setup-complete

setup: $(SETUP_SENTINEL)

$(SETUP_SENTINEL):
	chmod +x setup.sh
	./setup.sh

# Run locally (dev mode) — Tailscale-aware: reads TAILSCALE_ENABLED from .env
run:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if [ "$${TAILSCALE_ENABLED:-false}" = "true" ]; then \
		echo "[INFO] Tailscale enabled — checking connection..."; \
		if ! command -v tailscale >/dev/null 2>&1; then \
			echo "[ERROR] Tailscale is not installed. Install it or set TAILSCALE_ENABLED=false in .env."; \
			exit 1; \
		fi; \
		if grep -qi microsoft /proc/version 2>/dev/null && ! pgrep -x tailscaled >/dev/null 2>&1; then \
			echo "[INFO] Starting Tailscale daemon (WSL2)..."; \
			sudo mkdir -p /var/run/tailscale /var/lib/tailscale; \
			sudo tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock >/dev/null 2>&1 & sleep 2; \
		fi; \
		if ! tailscale status >/dev/null 2>&1; then \
			echo "[INFO] Connecting to Tailscale..."; \
			if [ -n "$${TAILSCALE_AUTH_KEY:-}" ]; then \
				sudo tailscale up --authkey="$${TAILSCALE_AUTH_KEY}" --hostname=$${TAILSCALE_HOSTNAME:-hummingbot-api} --accept-dns=true; \
			else \
				sudo tailscale up --hostname=$${TAILSCALE_HOSTNAME:-hummingbot-api} --accept-dns=true; \
			fi; \
		else \
			echo "[INFO] Tailscale already connected."; \
		fi; \
	fi
	docker compose up emqx postgres -d
	conda run --no-capture-output -n hummingbot-api uvicorn main:app --reload

# Deploy with Docker (Tailscale-aware: reads TAILSCALE_ENABLED from .env)
deploy: $(SETUP_SENTINEL)
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if [ "$${TAILSCALE_ENABLED:-false}" = "true" ]; then \
		echo "[INFO] Deploying with Tailscale sidecar..."; \
		docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d; \
	else \
		docker compose up -d; \
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
# Show Tailscale connection status
tailscale-status:
	@if command -v tailscale >/dev/null 2>&1; then \
		tailscale status; \
	else \
		echo "Tailscale is not installed or not on PATH."; \
	fi
