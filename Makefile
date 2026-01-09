.PHONY: setup run deploy stop install uninstall build install-pre-commit

setup:
	chmod +x setup.sh
	./setup.sh

# Run locally (dev mode)
run:
	docker compose up emqx postgres -d
	conda run --no-capture-output -n hummingbot-api uvicorn main:app --reload

# Deploy with Docker
deploy:
	chmod +x setup.sh
	./setup.sh
	docker compose up -d

# Stop all services
stop:
	docker compose down

# Install conda environment
install:
	@# Check if conda is available
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

install-pre-commit:
	conda run -n hummingbot-api pip install pre-commit
	conda run -n hummingbot-api pre-commit install

# Build Docker image
build:
	docker build -t hummingbot/hummingbot-api:latest .
