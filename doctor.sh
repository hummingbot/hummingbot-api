#!/bin/bash
# Verify a hummingbot-api install's dependencies, configuration, containers,
# port exposure and API reachability.
#
#   make doctor
#
# Read-only: nothing here starts, stops, or writes anything. Exits non-zero
# when a check actually fails, so CI (or `make deploy && make doctor`) can act
# on the result. Warnings alone exit 0 -- they are advice, not breakage.
#
# Deliberately plain bash with no conda/Python dependency: the Docker deploy
# path never creates the conda env, and a doctor that cannot run on the most
# common install is not much of a doctor. Mirrors the report layout of
# Condor's `make doctor` so the two read as one tool.

set -uo pipefail

cd "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1

# ── Palette ──────────────────────────────────────────

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BOLD=""; DIM=""; RESET=""
fi

# ── Layout ───────────────────────────────────────────

MIN_WIDTH=46
MAX_WIDTH=100
NAME_WIDTH=26
GUTTER=8   # 4-space indent + badge + space + 2 trailing spaces

term_cols="$( (tput cols) 2>/dev/null || echo 80 )"
[[ "$term_cols" =~ ^[0-9]+$ ]] || term_cols=80
WIDTH=$(( term_cols - 2 ))
[ "$WIDTH" -gt "$MAX_WIDTH" ] && WIDTH=$MAX_WIDTH
[ "$WIDTH" -lt "$MIN_WIDTH" ] && WIDTH=$MIN_WIDTH

PASSED=0; WARNED=0; FAILED=0

frame() { printf '%s%s%s\n' "${1:-$BOLD}" "$(printf '═%.0s' $(seq 1 $WIDTH))" "$RESET"; }

section() {
    printf '  %s%s%s\n' "$BOLD" "$1" "$RESET"
    printf '  %s%s%s\n' "$DIM" "$(printf '─%.0s' $(seq 1 $((WIDTH - 2))))" "$RESET"
}

# row STATE NAME DETAIL -- detail wraps under the detail column instead of
# spilling past the frame.
row() {
    local state="$1" name="$2" detail="$3" color badge
    case "$state" in
        ok)   color="$GREEN";  badge="✓"; PASSED=$((PASSED + 1)) ;;
        warn) color="$YELLOW"; badge="!"; WARNED=$((WARNED + 1)) ;;
        *)    color="$RED";    badge="✗"; FAILED=$((FAILED + 1)) ;;
    esac
    local body=$(( WIDTH - GUTTER - NAME_WIDTH ))
    [ "$body" -lt 24 ] && body=24
    local indent first=true line
    indent="$(printf '%*s' $((GUTTER + NAME_WIDTH)) '')"
    while IFS= read -r line; do
        if [ "$first" = true ]; then
            printf '    %s%s%s %-*s  %s%s%s\n' \
                "$color" "$badge" "$RESET" "$NAME_WIDTH" "$name" "$DIM" "$line" "$RESET"
            first=false
        else
            printf '%s%s%s%s\n' "$indent" "$DIM" "$line" "$RESET"
        fi
    done < <(printf '%s\n' "$detail" | fold -s -w "$body" | sed -E 's/[[:space:]]+$//')
}

# ── .env access ──────────────────────────────────────

# Read one KEY out of .env without executing it: `source`-ing a file whose
# values may contain shell metacharacters (a password with parentheses is
# enough) is a syntax error that silently drops every variable below it.
env_get() {
    local key="$1" line
    [ -f .env ] || return 0
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" .env 2>/dev/null | tail -1)" || return 0
    [ -n "$line" ] || return 0
    line="${line#*=}"
    line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    case "$line" in
        \"*\") line="${line%\"}"; line="${line#\"}" ;;
        \'*\') line="${line%\'}"; line="${line#\'}" ;;
    esac
    printf '%s' "$line"
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$1"
}

container_status() {
    docker ps --filter "name=^$1$" --format '{{.Status}}' 2>/dev/null | head -1
}

# Local bind addresses currently LISTENing on a port, one per line.
listening_binds() {
    local port="$1"
    if has_cmd ss; then
        ss -H -ltn "( sport = :$port )" 2>/dev/null | awk '{print $4}'
    elif has_cmd lsof; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $(NF-1)}'
    fi
}

is_public_bind() {
    local host="${1%:*}"
    case "$host" in
        0.0.0.0|"*"|"::"|"[::]"|"") return 0 ;;
        *) return 1 ;;
    esac
}

# ── Header ───────────────────────────────────────────

echo ""
frame
printf '  %sHummingbot API Doctor%s\n' "$BOLD" "$RESET"
frame
echo ""

# ── Dependencies ─────────────────────────────────────

section "Dependencies"

if has_cmd docker; then
    row ok "docker" "$(docker --version 2>/dev/null || echo installed)"
    if docker info >/dev/null 2>&1; then
        row ok "docker daemon" "responding"
        DOCKER_UP=true
    else
        row fail "docker daemon" "not responding — start Docker Desktop, or \`sudo systemctl start docker\`"
        DOCKER_UP=false
    fi
else
    row fail "docker" "not found — https://docs.docker.com/get-docker/"
    DOCKER_UP=false
fi

if docker compose version >/dev/null 2>&1; then
    row ok "docker compose" "$(docker compose version --short 2>/dev/null || echo 'v2 plugin')"
elif has_cmd docker-compose; then
    row warn "docker compose" "only the legacy docker-compose v1 binary is available; the Makefile calls \`docker compose\`"
else
    row fail "docker compose" "not found — on Debian/Ubuntu: sudo apt-get install -y docker-compose-plugin"
fi

if has_cmd curl; then
    row ok "curl" "installed"
else
    row warn "curl" "not found — needed to health-check the API from the shell"
fi

echo ""

# ── Configuration ────────────────────────────────────

section "Configuration"

if [ ! -f .env ]; then
    row fail ".env" "not found — run \`make setup\`"
    ENV_OK=false
else
    ENV_OK=true
    row ok ".env" "present"

    HB_USERNAME="$(env_get USERNAME)"
    HB_PASSWORD="$(env_get PASSWORD)"
    HB_CONFIG_PASSWORD="$(env_get CONFIG_PASSWORD)"
    TS_ENABLED="$(env_get TAILSCALE_ENABLED)"
    TS_HOSTNAME="$(env_get TAILSCALE_HOSTNAME)"
    BIND_HOST="$(env_get API_BIND_HOST)"
    GATEWAY_PASSPHRASE="$(env_get GATEWAY_PASSPHRASE)"

    # Credentials that used to be shipped as defaults. This API can place
    # orders and read balances; a guessable password is the whole attack.
    weak_credential() {
        case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
            ""|admin|password|changeme|hummingbot|test|admin123) return 0 ;;
            *) return 1 ;;
        esac
    }

    # A guessable USERNAME is only half a credential -- it warns. A guessable
    # PASSWORD or CONFIG_PASSWORD is the whole thing, and fails.
    for pair in "USERNAME:$HB_USERNAME:warn" "PASSWORD:$HB_PASSWORD:fail" "CONFIG_PASSWORD:$HB_CONFIG_PASSWORD:fail"; do
        key="${pair%%:*}"; rest="${pair#*:}"
        value="${rest%:*}"; weak_state="${rest##*:}"
        if [ -z "$value" ]; then
            row fail "$key" "empty — set it in .env, then \`make deploy\` to apply"
        elif weak_credential "$value"; then
            row "$weak_state" "$key" "set to a well-known default — anyone scanning for this API will try it first. Change it in .env, then \`make deploy\`"
        elif [ "${#value}" -lt 12 ] && [ "$key" != "USERNAME" ]; then
            row warn "$key" "shorter than 12 characters — fine on a laptop, weak on anything with a public IP"
        else
            row ok "$key" "set"
        fi
    done

    if [ -z "$GATEWAY_PASSPHRASE" ]; then
        row warn "GATEWAY_PASSPHRASE" "empty — only matters if you use Gateway for DEX trading"
    elif weak_credential "$GATEWAY_PASSPHRASE"; then
        row warn "GATEWAY_PASSPHRASE" "set to a well-known default — it unlocks Gateway's wallet keys. Change it if you use Gateway"
    else
        row ok "GATEWAY_PASSPHRASE" "set"
    fi

    if [ "$TS_ENABLED" = "true" ]; then
        if [ "$BIND_HOST" = "127.0.0.1" ]; then
            row ok "API_BIND_HOST" "127.0.0.1 (Tailscale serve exposes 8000 on the tailnet)"
        else
            row fail "API_BIND_HOST" "TAILSCALE_ENABLED=true but API_BIND_HOST=${BIND_HOST:-unset} — port 8000 stays published on every interface alongside the tailnet. Set API_BIND_HOST=127.0.0.1 and \`make deploy\`"
        fi
    else
        row ok "TAILSCALE_ENABLED" "false — port 8000 is published on ${BIND_HOST:-0.0.0.0}"
    fi
fi

echo ""

# ── Containers ───────────────────────────────────────

section "Containers"

if [ "$DOCKER_UP" != true ]; then
    row warn "stack" "skipped — Docker is not available"
else
    # container_name is pinned in docker-compose.yml, so these are exact --
    # emqx runs as hummingbot-broker and postgres as hummingbot-postgres,
    # which is not something `docker ps | grep emqx` would ever find.
    if container_running hummingbot-api; then
        row ok "hummingbot-api" "$(container_status hummingbot-api)"
    elif pgrep -f "uvicorn main[:]app" >/dev/null 2>&1; then
        row ok "hummingbot-api" "running from source (uvicorn), not the Docker deploy"
    else
        row fail "hummingbot-api" "not running — start it with \`make deploy\` (Docker) or \`make run\` (source)"
    fi

    for entry in "hummingbot-broker:EMQX broker" "hummingbot-postgres:Postgres"; do
        cname="${entry%%:*}"; label="${entry#*:}"
        if container_running "$cname"; then
            row ok "$label" "$cname — $(container_status "$cname")"
        else
            row fail "$label" "container $cname is not running — the API needs it; \`make deploy\`, or \`docker compose up emqx postgres -d\` for a source run"
        fi
    done
fi

echo ""

# ── Port exposure ────────────────────────────────────

section "Port exposure"

# 5432/1883/18083 are bound to 127.0.0.1 in docker-compose.yml. An older
# deploy that has not been recreated since still has them on every interface,
# and nothing about `docker compose ps` makes that visible.
for entry in "5432:Postgres" "1883:EMQX MQTT" "18083:EMQX dashboard"; do
    port="${entry%%:*}"; label="${entry#*:}"
    binds="$(listening_binds "$port")"
    if [ -z "$binds" ]; then
        row ok "$label ($port)" "not listening"
        continue
    fi
    public=""
    while IFS= read -r bind; do
        [ -n "$bind" ] || continue
        if is_public_bind "$bind"; then public="$bind"; break; fi
    done <<< "$binds"
    if [ -n "$public" ]; then
        row fail "$label ($port)" "bound to $public (all interfaces) — nothing external needs it. Recreate the stack with \`make deploy\` to pick up the loopback-only binding"
    else
        row ok "$label ($port)" "127.0.0.1 only"
    fi
done

api_binds="$(listening_binds 8000)"
api_public=""
while IFS= read -r bind; do
    [ -n "$bind" ] || continue
    if is_public_bind "$bind"; then api_public="$bind"; break; fi
done <<< "$api_binds"

if [ -z "$api_binds" ]; then
    row warn "API (8000)" "nothing listening — the API is not up"
elif [ -n "$api_public" ] && [ "${TS_ENABLED:-}" = "true" ]; then
    row fail "API (8000)" "bound to $api_public while Tailscale is enabled — it is on the tailnet AND every other interface at once"
elif [ -n "$api_public" ]; then
    row warn "API (8000)" "bound to $api_public — fine on a trusted LAN, exposed on a public VPS. Enable Tailscale, or firewall the port"
else
    row ok "API (8000)" "127.0.0.1 only"
fi

echo ""

# ── Tailscale ────────────────────────────────────────

section "Tailscale"

if [ "${TS_ENABLED:-}" != "true" ]; then
    row ok "Tailscale" "not enabled (TAILSCALE_ENABLED is not true)"
else
    TS_EXEC=""
    if container_running hummingbot-tailscale; then
        TS_EXEC="docker exec hummingbot-tailscale tailscale"
        row ok "Sidecar" "hummingbot-tailscale container is running"
    elif has_cmd tailscale; then
        TS_EXEC="tailscale"
        row ok "Client" "installed locally"
    else
        row fail "Tailscale" "TAILSCALE_ENABLED=true but neither the sidecar container nor a local tailscale client is present — \`make deploy\` (Docker) or \`make run\` (source)"
    fi

    if [ -n "$TS_EXEC" ]; then
        if ts_status="$($TS_EXEC status 2>&1)"; then
            row ok "Tailnet" "$(printf '%s' "$ts_status" | grep -v '^[[:space:]]*$' | head -1)"
        else
            row fail "Tailnet" "not connected — check TAILSCALE_AUTH_KEY (keys expire) and re-run \`make deploy\`"
        fi
        # Joining the tailnet is not the same as being reachable on it: with
        # API_BIND_HOST=127.0.0.1, `tailscale serve` is the only thing that
        # forwards the tailnet's :8000 anywhere.
        if ts_serve="$($TS_EXEC serve status 2>&1)" && printf '%s' "$ts_serve" | grep -q "8000"; then
            row ok "Serve (port 8000)" "proxied to the tailnet as http://${TS_HOSTNAME:-hummingbot-api}:8000"
        else
            row fail "Serve (port 8000)" "the node is on the tailnet but port 8000 is not proxied — with API_BIND_HOST=127.0.0.1 nothing can reach the API at all. Check tailscale-serve.json is mounted, then \`make deploy\`"
        fi
    fi
fi

echo ""

# ── API reachability ─────────────────────────────────

section "API"

if ! has_cmd curl; then
    row warn "Reachability" "skipped — curl is not installed"
elif [ "$ENV_OK" != true ]; then
    row warn "Reachability" "skipped — no .env to read credentials from"
else
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 8 \
        -u "${HB_USERNAME}:${HB_PASSWORD}" http://localhost:8000/ 2>/dev/null)"
    case "$code" in
        200)
            row ok "Authenticated request" "200 from http://localhost:8000/"
            # A 200 without credentials would mean auth is not being enforced.
            anon="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 8 \
                http://localhost:8000/ 2>/dev/null)"
            if [ "$anon" = "200" ]; then
                row fail "Auth enforcement" "an unauthenticated request also returned 200 — the API is answering anyone who can reach the port"
            else
                row ok "Auth enforcement" "unauthenticated request rejected ($anon)"
            fi
            ;;
        401|403)
            row fail "Authenticated request" "$code — the API is up but USERNAME/PASSWORD in .env do not match what it is running with. Restart it after changing them: \`make deploy\`"
            ;;
        000|"")
            row fail "Authenticated request" "no response from http://localhost:8000/ — the API is not listening. \`make deploy\` (Docker) or \`make run\` (source)"
            ;;
        *)
            row warn "Authenticated request" "unexpected HTTP $code from http://localhost:8000/ — check \`docker compose logs hummingbot-api\`"
            ;;
    esac
fi

echo ""

# ── Tally ────────────────────────────────────────────

if [ "$FAILED" -gt 0 ]; then
    border="$RED"; exit_code=1
elif [ "$WARNED" -gt 0 ]; then
    border="$YELLOW"; exit_code=0
else
    border="$GREEN"; exit_code=0
fi

frame "$border"
printf '  %s✓ %d passed%s   %s! %d warning(s)%s   %s✗ %d failed%s\n' \
    "$GREEN" "$PASSED" "$RESET" "$YELLOW" "$WARNED" "$RESET" "$RED" "$FAILED" "$RESET"
frame "$border"
echo ""

exit "$exit_code"
