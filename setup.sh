#!/bin/bash
# Hummingbot API Setup - Creates .env with sensible defaults (Mac/Linux/WSL2)
# - On Linux (apt-based): installs build deps (gcc, build-essential)
# - Ensures Docker + Docker Compose are available (auto-installs on Linux via get.docker.com)
# - Idempotent: safe to run multiple times, skips already-completed steps
# - Verbose output: shows all installation progress directly
# - Fixed: Removed apt-get upgrade, uses /dev/tty for prompts

set -euo pipefail

CERTS_ONLY=false
if [[ "${1:-}" == "--certs-only" ]]; then
  CERTS_ONLY=true
fi

echo "Hummingbot API Setup"
echo ""

# --------------------------
# State Tracking Variables
# --------------------------
APT_CACHE_UPDATED=false
DOCKER_ALREADY_PRESENT=false
COMPOSE_ALREADY_PRESENT=false

has_cmd() { command -v "$1" >/dev/null 2>&1; }

prompt_tty() {
  local message="$1"
  local default_value="${2:-}"
  local value=""
  if [[ -c /dev/tty ]] && [[ -r /dev/tty ]]; then
    read -p "$message" value < /dev/tty
  else
    read -p "$message" value
  fi
  echo "${value:-$default_value}"
}

prompt_yes_no() {
  local message="$1"
  local default_value="${2:-n}"
  local value
  value="$(prompt_tty "$message" "$default_value")"
  [[ "$value" =~ ^[Yy]$ ]]
}

ensure_openssl() {
  if has_cmd openssl; then
    return 0
  fi

  if is_linux && has_cmd apt-get; then
    need_sudo_or_die
    echo "[INFO] Installing openssl..."
    safe_apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y openssl
    return 0
  fi

  echo "[WARN] openssl is not available. HTTPS cert generation will be skipped."
  return 1
}

generate_ssl_bundle() {
  local certs_dir="$SCRIPT_DIR/certs"
  local ca_key="$certs_dir/ca.key"
  local ca_pem="$certs_dir/ca.pem"
  local server_key="$certs_dir/server.key"
  local server_csr="$certs_dir/server.csr"
  local server_pem="$certs_dir/server.pem"
  local client_key="$certs_dir/client.key"
  local client_csr="$certs_dir/client.csr"
  local client_pem="$certs_dir/client.pem"
  local serial_file="$certs_dir/ca.srl"
  local host="$1"
  local generate_mtls="$2"

  mkdir -p "$certs_dir"

  openssl genrsa -out "$ca_key" 4096 >/dev/null 2>&1
  openssl req -x509 -new -nodes -key "$ca_key" -sha256 -days 3650 \
    -out "$ca_pem" -subj "/CN=Hummingbot Local CA" >/dev/null 2>&1

  openssl genrsa -out "$server_key" 2048 >/dev/null 2>&1
  openssl req -new -key "$server_key" -out "$server_csr" -subj "/CN=$host" >/dev/null 2>&1
  openssl x509 -req -in "$server_csr" -CA "$ca_pem" -CAkey "$ca_key" -CAcreateserial \
    -out "$server_pem" -days 825 -sha256 >/dev/null 2>&1

  if [[ "$generate_mtls" == "true" ]]; then
    openssl genrsa -out "$client_key" 2048 >/dev/null 2>&1
    openssl req -new -key "$client_key" -out "$client_csr" -subj "/CN=condor-client" >/dev/null 2>&1
    openssl x509 -req -in "$client_csr" -CA "$ca_pem" -CAkey "$ca_key" -CAserial "$serial_file" \
      -out "$client_pem" -days 825 -sha256 >/dev/null 2>&1
  fi

  rm -f "$server_csr" "$client_csr"
}

resolve_script_dir() {
  local src="${BASH_SOURCE[0]}"
  while [ -h "$src" ]; do
    local dir
    dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"
    src="$(readlink "$src")"
    [[ "$src" != /* ]] && src="$dir/$src"
  done
  cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd
}

SCRIPT_DIR="$(resolve_script_dir)"

# --------------------------
# OS / Environment Detection
# --------------------------
OS="$(uname -s || true)"
ARCH="$(uname -m || true)"

is_linux() { [[ "${OS}" == "Linux" ]]; }
is_macos() { [[ "${OS}" == "Darwin" ]]; }

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
# APT Cache Management (Linux)
# --------------------------
safe_apt_update() {
  # Only run apt-get update once per script execution
  if [ "$APT_CACHE_UPDATED" = false ]; then
    echo "[INFO] Updating apt cache..."
    sudo env DEBIAN_FRONTEND=noninteractive apt-get update
    APT_CACHE_UPDATED=true
  fi
}

# --------------------------
# Package Check Utilities
# --------------------------
is_package_installed() {
  # Check if a Debian package is installed
  # Usage: is_package_installed package-name
  dpkg -l "$1" 2>/dev/null | grep -q "^ii"
}

# --------------------------
# Linux Dependencies
# --------------------------
install_linux_build_deps() {
  if has_cmd apt-get; then
    # Check if build dependencies are already installed
    if is_package_installed build-essential && has_cmd gcc; then
      echo "[OK] Build dependencies (gcc, build-essential) already installed. Skipping."
      return 0
    fi
    
    need_sudo_or_die
    echo "[INFO] Installing build dependencies (gcc, build-essential)..."

    safe_apt_update
    
    # REMOVED: apt-get upgrade -y 
    # This was causing failures due to system-wide package upgrades
    # apt-get install will get the latest available versions anyway
    
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y gcc build-essential

    echo "[OK] Build dependencies installed."
  else
    echo "[WARN] Detected Linux, but 'apt-get' is not available. Skipping build dependency install."
  fi
}

ensure_curl_on_linux() {
  if has_cmd curl; then
    echo "[OK] curl is already installed."
    return 0
  fi

  if has_cmd apt-get; then
    need_sudo_or_die
    echo "[INFO] Installing curl (required for Docker install script)..."
    safe_apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
    echo "[OK] curl installed."
    return 0
  fi

  echo "[WARN] curl is not installed and apt-get is unavailable. Please install curl and re-run."
  return 1
}

# --------------------------
# Docker Install / Validation
# --------------------------
check_user_in_docker_group() {
  # Check if current user is already in docker group
  if [[ "${EUID}" -eq 0 ]]; then
    # Running as root, no need for docker group
    return 0
  fi
  
  if has_cmd getent && getent group docker >/dev/null 2>&1; then
    if id -nG "$USER" 2>/dev/null | grep -qw docker; then
      return 0
    fi
  fi
  
  return 1
}

add_user_to_docker_group() {
  # Only add user to docker group if not already a member
  if check_user_in_docker_group; then
    echo "[OK] User '$USER' is already in the 'docker' group."
    return 0
  fi
  
  if has_cmd getent && getent group docker >/dev/null 2>&1; then
    if [[ "${EUID}" -ne 0 ]]; then
      echo "[INFO] Adding current user to 'docker' group (may require re-login)..."
      sudo usermod -aG docker "$USER" >/dev/null 2>&1 || true
      echo "[OK] User added to docker group. You may need to log out and back in for this to take effect."
    fi
  fi
}

install_docker_linux() {
  need_sudo_or_die
  ensure_curl_on_linux

  echo "[INFO] Docker not found. Installing Docker using get.docker.com script..."
  curl -fsSL https://get.docker.com -o get-docker.sh
  sudo sh get-docker.sh
  rm -f get-docker.sh

  if has_cmd systemctl; then
    if systemctl is-system-running >/dev/null 2>&1; then
      echo "[INFO] Enabling and starting Docker service..."
      sudo systemctl enable docker 2>/dev/null || true
      sudo systemctl start docker 2>/dev/null || true
    fi
  fi

  add_user_to_docker_group
}

ensure_docker_and_compose() {
  if is_linux; then
    # Check Docker installation
    if docker_ok; then
      echo "[OK] Docker already installed: $(docker --version 2>/dev/null || echo 'version unknown')"
      DOCKER_ALREADY_PRESENT=true
      
      # Even if Docker is installed, ensure user is in docker group
      add_user_to_docker_group
    else
      # Check if Docker binary exists but isn't in PATH
      if [ -x "/usr/bin/docker" ] || [ -x "/usr/local/bin/docker" ]; then
        echo "[INFO] Docker found but not in current PATH. Adding to PATH..."
        export PATH="/usr/bin:/usr/local/bin:$PATH"
        
        if docker_ok; then
          echo "[OK] Docker is now accessible: $(docker --version 2>/dev/null || echo 'version unknown')"
          DOCKER_ALREADY_PRESENT=true
          add_user_to_docker_group
        else
          install_docker_linux
        fi
      else
        install_docker_linux
      fi
    fi

    # Verify Docker is actually working
    if ! docker_ok; then
      echo "ERROR: Docker installation did not succeed or 'docker' is still not on PATH."
      echo "       Try opening a new shell and re-running, or verify Docker installation."
      exit 1
    fi

    # Check Docker Compose installation
    if docker_compose_ok; then
      echo "[OK] Docker Compose already available"
      COMPOSE_ALREADY_PRESENT=true
      
      # Show which version we detected
      if docker compose version >/dev/null 2>&1; then
        echo "[OK] Using Docker Compose plugin: $(docker compose version 2>/dev/null || echo 'version unknown')"
      else
        echo "[OK] Using standalone docker-compose: $(docker-compose version 2>/dev/null || echo 'version unknown')"
      fi
    else
      # Try to install docker-compose-plugin
      if has_cmd apt-get; then
        # Check if plugin package is already installed but not working
        if is_package_installed docker-compose-plugin; then
          echo "[WARN] docker-compose-plugin package is installed but not functioning properly."
          echo "[INFO] Attempting to reinstall docker-compose-plugin..."
          need_sudo_or_die
          safe_apt_update
          sudo env DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y docker-compose-plugin || true
        else
          need_sudo_or_die
          echo "[INFO] Docker Compose not found. Attempting to install docker-compose-plugin..."
          safe_apt_update
          sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin || true
        fi
      fi
    fi

    # Final verification of Docker Compose
    if ! docker_compose_ok; then
      echo "ERROR: Docker Compose is not available."
      echo "       Expected either 'docker compose' (v2) or 'docker-compose' (v1)."
      echo "       On Ubuntu/Debian, try: sudo apt-get install -y docker-compose-plugin"
      exit 1
    fi
    
  elif is_macos; then
    if ! docker_ok || ! docker_compose_ok; then
      echo "ERROR: Docker and/or Docker Compose not found on macOS."
      echo "       Install Docker Desktop for Mac (Apple Silicon or Intel) and re-run this script."
      echo "       After installation, ensure 'docker' works in this terminal (you may need a new shell)."
      exit 1
    fi
    
    echo "[OK] Docker detected: $(docker --version 2>/dev/null || echo 'version unknown')"
    if docker compose version >/dev/null 2>&1; then
      echo "[OK] Docker Compose detected: $(docker compose version 2>/dev/null || echo 'version unknown')"
    else
      echo "[OK] Docker Compose detected: $(docker-compose version 2>/dev/null || echo 'version unknown')"
    fi
    
  else
    echo "[WARN] Unsupported/unknown OS '${OS}'. Proceeding without installing OS-level dependencies."
    if ! docker_ok || ! docker_compose_ok; then
      echo "ERROR: Docker and/or Docker Compose not found."
      exit 1
    fi
    
    echo "[OK] Docker detected: $(docker --version 2>/dev/null || echo 'version unknown')"
    if docker compose version >/dev/null 2>&1; then
      echo "[OK] Docker Compose detected: $(docker compose version 2>/dev/null || echo 'version unknown')"
    else
      echo "[OK] Docker Compose detected: $(docker-compose version 2>/dev/null || echo 'version unknown')"
    fi
  fi
}

# --------------------------
# Pull Hummingbot Docker Image
# --------------------------
pull_hummingbot_image() {
  echo "[INFO] Pulling latest Hummingbot image (hummingbot/hummingbot:latest)..."
  if docker pull hummingbot/hummingbot:latest; then
    echo "[OK] Hummingbot image pulled successfully."
  else
    echo "[WARN] Could not pull hummingbot/hummingbot:latest (network issue?). You may need to run 'docker pull hummingbot/hummingbot:latest' manually."
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

# Show summary of what was done
echo ""
if [ "$DOCKER_ALREADY_PRESENT" = true ] && [ "$COMPOSE_ALREADY_PRESENT" = true ]; then
  echo "[OK] All dependencies were already installed. No changes made."
elif [ "$DOCKER_ALREADY_PRESENT" = true ]; then
  echo "[OK] Docker was already installed. Docker Compose has been set up."
elif [ "$COMPOSE_ALREADY_PRESENT" = true ]; then
  echo "[OK] Docker has been installed. Docker Compose was already available."
else
  echo "[OK] Docker and Docker Compose have been installed."
fi

echo ""

# Always pull latest Hummingbot image (first install and upgrade)
pull_hummingbot_image

echo ""

# --------------------------
# Existing .env creation flow
# --------------------------
if [ "$CERTS_ONLY" = false ] && [ -f ".env" ]; then
  echo ".env file already exists. Skipping setup."
  echo ""
  
  # Ensure sentinel file exists
  if [ ! -f ".setup-complete" ]; then
    touch .setup-complete
  fi
  
  exit 0
fi

# Clear screen before prompting user (only if running interactively)
if [[ -t 0 ]] && [[ -c /dev/tty ]]; then
  if has_cmd clear; then
    clear
  else
    printf "\033c"
  fi
fi

echo "Hummingbot API Setup"
echo ""

# Use /dev/tty for prompts to work correctly when called from parent scripts
if [ "$CERTS_ONLY" = false ]; then
  USERNAME="$(prompt_tty "API username [default: admin]: " "admin")"
  PASSWORD="$(prompt_tty "API password [default: admin]: " "admin")"
  CONFIG_PASSWORD="$(prompt_tty "Config password [default: admin]: " "admin")"
else
  USERNAME="${USERNAME:-admin}"
  PASSWORD="${PASSWORD:-admin}"
  CONFIG_PASSWORD="${CONFIG_PASSWORD:-admin}"
fi

SSL_ENABLED=false
SSL_PORT=8443
SSL_HOST=localhost
SSL_CERT_PATH="$SCRIPT_DIR/certs/server.pem"
SSL_KEY_PATH="$SCRIPT_DIR/certs/server.key"
SSL_CA_PATH="$SCRIPT_DIR/certs/ca.pem"
SSL_CLIENT_CERT_PATH="$SCRIPT_DIR/certs/client.pem"
SSL_CLIENT_KEY_PATH="$SCRIPT_DIR/certs/client.key"
SSL_GENERATE_MTLS=false

if prompt_yes_no "Enable HTTPS cert generation for Hummingbot API? [y/N]: " "n"; then
  if ensure_openssl; then
    SSL_ENABLED=true
    SSL_PORT="$(prompt_tty "HTTPS port [default: 8443]: " "8443")"
    SSL_HOST="$(prompt_tty "Certificate hostname/CN [default: localhost]: " "localhost")"
    if prompt_yes_no "Generate client certificate pair for mTLS as well? [y/N]: " "n"; then
      SSL_GENERATE_MTLS=true
    fi
    echo "[INFO] Generating SSL certificates under $SCRIPT_DIR/certs ..."
    generate_ssl_bundle "$SSL_HOST" "$SSL_GENERATE_MTLS"
    echo "[OK] Certificates generated."
  fi
fi

if [ "$CERTS_ONLY" = true ]; then
  if [ "$SSL_ENABLED" = true ]; then
    echo ""
    echo "Certificate paths (copy into Condor config):"
    echo "  CA bundle:      $SSL_CA_PATH"
    echo "  Server cert:    $SSL_CERT_PATH"
    echo "  Server key:     $SSL_KEY_PATH"
    if [ "$SSL_GENERATE_MTLS" = true ]; then
      echo "  Client cert:    $SSL_CLIENT_CERT_PATH"
      echo "  Client key:     $SSL_CLIENT_KEY_PATH"
    fi
  fi
  exit 0
fi

cat > .env << EOF
# Hummingbot API Configuration
USERNAME=$USERNAME
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

# HTTPS (optional)
SSL_ENABLED=$SSL_ENABLED
SSL_PORT=$SSL_PORT
SSL_CERT_PATH=$SSL_CERT_PATH
SSL_KEY_PATH=$SSL_KEY_PATH
SSL_CA_PATH=$SSL_CA_PATH
SSL_CLIENT_CERT_PATH=$SSL_CLIENT_CERT_PATH
SSL_CLIENT_KEY_PATH=$SSL_CLIENT_KEY_PATH

# Paths
BOTS_PATH=$(pwd)
EOF

touch .setup-complete

echo ""
echo ".env created successfully!"
echo ""
echo "Next steps:"
echo ""
echo "Option 1: Start all services with Docker (recommended)"
echo "  make deploy"
echo ""
echo "Option 2: Run API locally (dev mode)"
echo "  make install   # Creates the conda environment - Note: Please install the latest Anaconda version manually"
echo "  make run       # Run API"
echo "  make run-https # Run API with generated HTTPS certs (if enabled)"
if [ "$SSL_ENABLED" = true ]; then
  echo ""
  echo "HTTPS cert paths for Condor:"
  echo "  CA bundle:   $SSL_CA_PATH"
  echo "  Server cert: $SSL_CERT_PATH"
  echo "  Server key:  $SSL_KEY_PATH"
  if [ "$SSL_GENERATE_MTLS" = true ]; then
    echo "  Client cert: $SSL_CLIENT_CERT_PATH"
    echo "  Client key:  $SSL_CLIENT_KEY_PATH"
  fi
fi
echo ""
