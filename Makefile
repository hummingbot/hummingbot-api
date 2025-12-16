.PHONY: setup run deploy stop install uninstall build install-pre-commit

# Conda detection
detect_conda_bin := $(shell bash -c 'if [ "${CONDA_EXE} " == " " ]; then \
    CONDA_EXE=$$((find /opt/conda/bin/conda || find ~/anaconda3/bin/conda || \
    find /usr/local/anaconda3/bin/conda || find ~/miniconda3/bin/conda || \
    find /root/miniconda/bin/conda || find ~/Anaconda3/Scripts/conda || \
    find $$CONDA/bin/conda) 2>/dev/null); fi; \
    echo $$(dirname $${CONDA_EXE})')
CONDA_BIN := $(detect_conda_bin)

# Setup - create .env file
setup:
	@./setup.sh

# Run locally (dev mode)
run:
	docker compose up emqx postgres -d
	uvicorn main:app --reload

# Deploy with Docker
deploy:
	docker compose up -d

# Stop all services
stop:
	docker compose down

# Install conda environment
install:
	@if conda env list | grep -q '^hummingbot-api '; then \
	    echo "Environment already exists."; \
	else \
	    conda env create -f environment.yml; \
	fi
	$(MAKE) install-pre-commit

uninstall:
	conda env remove -n hummingbot-api -y

install-pre-commit:
	@/bin/bash -c 'source "${CONDA_BIN}/activate" hummingbot-api && \
	if ! conda list pre-commit | grep pre-commit &> /dev/null; then \
	    pip install pre-commit; \
	fi && pre-commit install'

# Build Docker image
build:
	docker build -t hummingbot/hummingbot-api:latest .
