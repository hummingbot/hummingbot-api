#!/bin/bash
# Hummingbot API Setup - Creates .env with sensible defaults (Mac/Linux/WSL2)
# - On Linux/WSL (apt-based): installs build deps (gcc, build-essential)
# - Ensures Docker + Docker Compose are available (auto-installs on Linux/WSL via get.docker.com)

set -euo pipefail

echo "Hummingbot API Setup"
echo ""

# --------------------------
# OS / Environment Detection
# --------------------------
OS="$(uname -s || true)"
ARCH="$(uname -m || true)"

is_linux() { [[ "${OS}" == "Linux" ]]; }
is_macos() { [[ "${OS}" == "Darwin" ]]; }

is_wsl() {
  if [[ -f /proc/version ]] && grep -qiE "microsoft|wsl" /proc/version; then
    return 0
  fi
  if [[ -f /proc/sys/kernel/osrelease ]] && grep -qiE "microsoft|wsl" /proc/sys/kernel/osrelease; then
    return 0
  fi
  return 1
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

docker_ok() { has_cmd docker; }

docker_compose_ok() {
  if has_cmd docker && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  if has_cmd docker-compose && docker-compose version >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

need_sudo_or_die() {
  if ! has_cmd sudo; then
    echo "ERROR: 'sudo' is required for dependency installation on this system."
    echo "Please install sudo (or run as root) and re-run this script."
    exit 1
  fi
}

# --------------------------
# Linux/WSL Dependencies
# --------------------------
install_linux_build_deps() {
  if has_cmd apt-get; then
    need_sudo_or_die
    echo "[INFO] Detected Linux/WSL. Installing build dependencies (gcc, build-essential)..."
    sudo apt update
    sudo apt upgrade -y
    sudo apt install -y gcc build-essential
  else
    echo "[WARN] Detected Linux/WSL, but 'apt-get' is not available. Skipping build dependency install."
    echo "       Install equivalents of gcc + build-essential using your distro's package manager."
  fi
}

ensure_curl_on_linux() {
  if has_cmd curl; then
    return 0
  fi

  if has_cmd apt-get; then
    need_sudo_or_die
    echo "[INFO] Installing curl (required for Docker install script)..."
    sudo apt update
    sudo apt install -y curl ca-certificates
    return 0
  fi

  echo "[WARN] curl is not installed and apt-get is unavailable. Please install curl and re-run."
  return 1
}

# --------------------------
# Docker Install / Validation
# --------------------------
install_docker_linux() {
  ensure_curl_on_linux

  echo "[INFO] Docker not found. Installing Docker using get.docker.com script..."
  curl -fsSL https://get.docker.com -o get-docker.sh
  sh get-docker.sh
  rm -f get-docker.sh

  if has_cmd systemctl; then
    if systemctl is-system-running >/dev/null 2>&1; then
      echo "[INFO] Enabling and starting Docker service..."
      sudo systemctl enable docker >/dev/null 2>&1 || true
      sudo systemctl start docker >/dev/null 2>&1 || true
    fi
  fi

  if has_cmd getent && getent group docker >/dev/null 2>&1; then
    if [[ "${EUID}" -ne 0 ]]; then
      echo "[INFO] Adding current user to 'docker' group (may require re-login)..."
      sudo usermod -aG docker "$USER" >/dev/null 2>&1 || true
    fi
  fi
}

ensure_docker_and_compose() {
  if is_linux; then
    if ! docker_ok; then
      install_docker_linux
    fi

    if ! docker_ok; then
      echo "ERROR: Docker installation did not succeed or 'docker' is still not on PATH."
      echo "       Try opening a new shell and re-running, or verify Docker installation."
      exit 1
    fi

    if ! docker_compose_ok; then
      if has_cmd apt-get; then
        need_sudo_or_die
        echo "[INFO] Docker Compose not found. Attempting to install docker-compose-plugin..."
        sudo apt update
        sudo apt install -y docker-compose-plugin || true
      fi
    fi

    if ! docker_compose_ok; then
      echo "ERROR: Docker Compose is not available."
      echo "       Expected either 'docker compose' (v2) or 'docker-compose' (v1)."
      echo "       On Ubuntu/Debian, try: sudo apt install -y docker-compose-plugin"
      exit 1
    fi
  elif is_macos; then
    if ! docker_ok || ! docker_compose_ok; then
      echo "ERROR: Docker and/or Docker Compose not found on macOS."
      echo "       Install Docker Desktop for Mac (Apple Silicon or Intel) and re-run this script."
      echo "       After installation, ensure 'docker' works in this terminal (you may need a new shell)."
      exit 1
    fi
  else
    echo "[WARN] Unsupported/unknown OS '${OS}'. Proceeding without installing OS-level dependencies."
    if ! docker_ok || ! docker_compose_ok; then
      echo "ERROR: Docker and/or Docker Compose not found."
      exit 1
    fi
  fi

  echo "[OK] Docker detected: $(docker --version 2>/dev/null || true)"
  if docker compose version >/dev/null 2>&1; then
    echo "[OK] Docker Compose detected: $(docker compose version 2>/dev/null || true)"
  else
    echo "[OK] Docker Compose detected: $(docker-compose version 2>/dev/null || true)"
  fi
}

# --------------------------
# Pre-flight (deps + docker)
# --------------------------
echo "[INFO] OS=${OS} ARCH=${ARCH}"
if is_linux; then
  install_linux_build_deps
fi

ensure_docker_and_compose

echo ""

# --------------------------
# Existing .env creation flow
# --------------------------
if [ -f ".env" ]; then
  echo ".env file already exists. Skipping setup."
  echo ""
  echo ""
  exit 0
fi

# Clear screen before prompting user
if has_cmd clear; then
  clear
else
  # Fallback: ANSI clear
  printf "\033c"
fi

echo "Hummingbot API Setup"
echo ""

read -p "API password [default: admin]: " PASSWORD
PASSWORD=${PASSWORD:-admin}

read -p "Config password [default: admin]: " CONFIG_PASSWORD
CONFIG_PASSWORD=${CONFIG_PASSWORD:-admin}

cat > .env << EOF
# Hummingbot API Configuration
USERNAME=admin
PASSWORD=$PASSWORD
CONFIG_PASSWORD=$CONFIG_PASSWORD
DEBUG_MODE=false

# MQTT Broker
BROKER_HOST=localhost
BROKER_PORT=1883
BROKER_USERNAME=admin
BROKER_PASSWORD=password

# Database (auto-configured by docker-compose)
DATABASE_URL=postgresql+asyncpg://hbot:hummingbot-api@localhost:5432/hummingbot_api

# Gateway (optional)
GATEWAY_URL=http://localhost:15888
GATEWAY_PASSPHRASE=admin

# Paths
BOTS_PATH=$(pwd)
EOF

echo ""
echo ".env created successfully!"
echo ""
echo "Next steps:"
echo ""
echo "Option 1: Start all services with Docker (recommended) "
echo "  make deploy " 
echo ""
echo "Option 2: Run API locally (dev mode)"
echo "  make install   # Creates the conda environment - Note: Please install the latest Anaconda version manually"
echo "  make run       # Run API "
echo ""
