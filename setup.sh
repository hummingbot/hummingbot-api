#!/bin/bash
# Hummingbot API Setup
# - Installs Docker on Linux (apt-based)
# - Creates .env with credentials and optional Tailscale config
# - Works for both Docker deployment and source/conda install
# - Idempotent: safe to re-run

set -euo pipefail

# ── Styling ────────────────────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

msg_ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
msg_info()  { echo -e "  ${CYAN}→${RESET} $1"; }
msg_warn()  { echo -e "  ${YELLOW}⚠${RESET} $1"; }
msg_error() { echo -e "  ${RED}✗${RESET} $1"; exit 1; }

prompt_visible() {
    local message="$1" default="${2:-}" var_name="$3" value=""
    if [[ -n "$default" ]]; then
        read -p "  $message [$default]: " value < /dev/tty 2>/dev/null || read -p "  $message [$default]: " value
    else
        read -p "  $message: " value < /dev/tty 2>/dev/null || read -p "  $message: " value
    fi
    printf -v "$var_name" '%s' "${value:-$default}"
}

prompt_secret() {
    local message="$1" default="${2:-}" var_name="$3" value=""
    if [[ -n "$default" ]]; then
        read -s -p "  $message [$default]: " value < /dev/tty 2>/dev/null || read -s -p "  $message [$default]: " value
    else
        read -s -p "  $message: " value < /dev/tty 2>/dev/null || read -s -p "  $message: " value
    fi
    echo
    printf -v "$var_name" '%s' "${value:-$default}"
}

# ── State tracking ─────────────────────────────────────────────────────────────
APT_CACHE_UPDATED=false
DOCKER_ALREADY_PRESENT=false
COMPOSE_ALREADY_PRESENT=false

has_cmd()   { command -v "$1" >/dev/null 2>&1; }
is_linux()  { [[ "$(uname -s)" == "Linux" ]]; }
is_macos()  { [[ "$(uname -s)" == "Darwin" ]]; }
is_wsl2()   { grep -qi microsoft /proc/version 2>/dev/null; }
docker_ok() { has_cmd docker; }

docker_compose_ok() {
    has_cmd docker && docker compose version >/dev/null 2>&1 && return 0
    has_cmd docker-compose && docker-compose version >/dev/null 2>&1 && return 0
    return 1
}

need_sudo_or_die() {
    has_cmd sudo && return 0
    msg_error "'sudo' is required. Install it or run as root."
}

safe_apt_update() {
    [ "$APT_CACHE_UPDATED" = true ] && return 0
    msg_info "Updating apt cache..."
    sudo env DEBIAN_FRONTEND=noninteractive apt-get update -q
    APT_CACHE_UPDATED=true
}

is_pkg_installed() { dpkg -l "$1" 2>/dev/null | grep -q "^ii"; }

check_user_in_docker_group() {
    [[ "${EUID}" -eq 0 ]] && return 0
    has_cmd getent && getent group docker >/dev/null 2>&1 && \
        id -nG "$USER" 2>/dev/null | grep -qw docker
}

add_user_to_docker_group() {
    check_user_in_docker_group && { msg_ok "User '$USER' already in docker group"; return 0; }
    has_cmd getent && getent group docker >/dev/null 2>&1 && [[ "${EUID}" -ne 0 ]] && \
        sudo usermod -aG docker "$USER" >/dev/null 2>&1 || true
    msg_ok "User added to docker group (re-login may be required)"
}

# ── Banner ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${BOLD}Hummingbot API Setup${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
msg_info "OS: $(uname -s)  ARCH: $(uname -m)"
echo ""

# ── Linux build deps ───────────────────────────────────────────────────────────
if is_linux && has_cmd apt-get; then
    if is_pkg_installed build-essential && has_cmd gcc; then
        msg_ok "Build dependencies already installed"
    else
        need_sudo_or_die
        msg_info "Installing build dependencies (gcc, build-essential)..."
        safe_apt_update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y gcc build-essential
        msg_ok "Build dependencies installed"
    fi
fi

# ── Docker ─────────────────────────────────────────────────────────────────────
install_docker_linux() {
    need_sudo_or_die
    if ! has_cmd curl; then
        msg_info "Installing curl..."
        safe_apt_update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
    fi
    msg_info "Installing Docker via get.docker.com..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    rm -f get-docker.sh
    if has_cmd systemctl && systemctl is-system-running >/dev/null 2>&1; then
        sudo systemctl enable docker 2>/dev/null || true
        sudo systemctl start docker 2>/dev/null || true
    fi
    add_user_to_docker_group
}

ensure_docker() {
    if is_linux; then
        if docker_ok; then
            msg_ok "Docker $(docker --version 2>/dev/null | head -1)"
            DOCKER_ALREADY_PRESENT=true
            add_user_to_docker_group
        else
            install_docker_linux
        fi
        docker_ok || msg_error "Docker installation failed. Open a new shell and re-run."

        if docker_compose_ok; then
            msg_ok "Docker Compose available"
            COMPOSE_ALREADY_PRESENT=true
        else
            if has_cmd apt-get; then
                need_sudo_or_die
                msg_info "Installing docker-compose-plugin..."
                safe_apt_update
                sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin || true
            fi
        fi
        docker_compose_ok || msg_error "Docker Compose not available. Try: sudo apt-get install -y docker-compose-plugin"

    elif is_macos; then
        docker_ok && docker_compose_ok || \
            msg_error "Docker Desktop not found. Install it from https://www.docker.com/products/docker-desktop and re-run."
        msg_ok "Docker $(docker --version 2>/dev/null | head -1)"
        msg_ok "Docker Compose available"
    else
        docker_ok && docker_compose_ok || msg_error "Docker and Docker Compose are required."
    fi
}

ensure_docker

echo ""
msg_info "Pulling latest Hummingbot image..."
if docker pull hummingbot/hummingbot:latest 2>/dev/null; then
    msg_ok "hummingbot/hummingbot:latest pulled"
else
    msg_warn "Could not pull hummingbot image — run 'docker pull hummingbot/hummingbot:latest' later"
fi
echo ""

# ── Idempotency check ──────────────────────────────────────────────────────────
if [ -f ".env" ]; then
    msg_ok ".env already exists — skipping setup"
    touch .setup-complete
    echo ""
    exit 0
fi

# ── Credentials ────────────────────────────────────────────────────────────────
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${BOLD}API Credentials${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""

prompt_visible "API username" "admin" USERNAME
prompt_secret  "API password" "admin" PASSWORD
prompt_secret  "Config password" "admin" CONFIG_PASSWORD

# ── Tailscale ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${BOLD}Tailscale (optional)${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
echo -e "  Use Tailscale to make this API securely accessible from Condor"
echo -e "  without exposing port 8000 to the public internet."
echo ""

prompt_visible "Enable Tailscale? [y/N]" "N" _use_tailscale

TAILSCALE_ENABLED=false
TAILSCALE_AUTH_KEY=""
TAILSCALE_HOSTNAME="hummingbot-api"

if [[ "${_use_tailscale:-}" =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "  ${CYAN}  How to get a Tailscale auth key:${RESET}"
    echo -e "  ${CYAN}    1. Create a free account at https://tailscale.com${RESET}"
    echo -e "  ${CYAN}    2. Go to: https://tailscale.com/admin/settings/keys${RESET}"
    echo -e "  ${CYAN}    3. Click 'Generate auth key'${RESET}"
    echo -e "  ${CYAN}    4. Check 'Reusable' for multiple server deployments${RESET}"
    echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    while true; do
        prompt_visible "Tailscale auth key (tskey-auth-...)" "" TAILSCALE_AUTH_KEY
        if [[ -z "${TAILSCALE_AUTH_KEY:-}" ]]; then
            msg_warn "Auth key cannot be empty"
            continue
        fi
        if [[ ! "$TAILSCALE_AUTH_KEY" =~ ^tskey-auth- ]]; then
            msg_warn "Auth key must start with 'tskey-auth-'"
            continue
        fi
        break
    done
    TAILSCALE_ENABLED=true
    msg_ok "Tailscale will be enabled — hostname: $TAILSCALE_HOSTNAME"
fi

# ── Write .env ─────────────────────────────────────────────────────────────────
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

# Paths
BOTS_PATH=$(pwd)

# Tailscale
TAILSCALE_ENABLED=$TAILSCALE_ENABLED
TAILSCALE_AUTH_KEY=$TAILSCALE_AUTH_KEY
TAILSCALE_HOSTNAME=$TAILSCALE_HOSTNAME
EOF

touch .setup-complete
msg_ok ".env created"

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Setup complete!${RESET}"
echo ""
echo -e "  ${BOLD}Docker deployment (recommended for VPS):${RESET}"
echo -e "    make deploy"
echo ""
echo -e "  ${BOLD}Source / dev mode (requires conda):${RESET}"
echo -e "    make install   ${CYAN}# create conda environment${RESET}"
echo -e "    make run       ${CYAN}# start API${RESET}"
if [ "$TAILSCALE_ENABLED" = true ]; then
    echo ""
    echo -e "  ${BOLD}Tailscale:${RESET}"
    echo -e "    Docker:  Tailscale sidecar starts automatically with 'make deploy'"
    echo -e "    Source:  Tailscale installs and connects automatically with 'make run'"
    echo -e "    API URL: http://$TAILSCALE_HOSTNAME:8000  ${CYAN}(Tailscale access)${RESET}"
    echo -e "    Status:  make tailscale-status"
    echo ""
    echo -e "  ${BOLD}Accessing from Condor:${RESET}"
    echo -e "  ${CYAN}  Install Tailscale on the Condor machine and connect with the same key:${RESET}"
    if is_wsl2; then
        echo -e "    curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --authkey=$TAILSCALE_AUTH_KEY"
    else
        echo -e "    Linux / WSL:  curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --authkey=$TAILSCALE_AUTH_KEY"
        echo -e "    macOS / Win:  https://tailscale.com/download — then run: sudo tailscale up --authkey=$TAILSCALE_AUTH_KEY"
    fi
fi
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
