#!/bin/bash
# Who, if anyone, already owns this host's tailnet interface?
#
# Sourced by setup.sh (to pick a default mode) and by the Makefile (to enforce
# one at deploy time). Prints exactly one word:
#
#   sidecar  our own hummingbot-tailscale container is running
#   native   a tailscaled is running on the host itself
#   none     nothing here owns a tailnet interface
#
# The predicate is deliberately about the RESOURCE, not about which product
# installed it. "Is Condor here?" is the wrong question: the most common real
# conflict is a VPS that already runs Tailscale for the admin's own SSH
# access, and no product check sees that. tailscale0 is the thing actually
# contended, it is path-independent, and on a host where the other component
# lives elsewhere there is simply nothing local to find.
#
# Why it matters: two kernel-mode tailscaled in one network namespace is
# fatal and silent. The second dies with
#   tstun.New("tailscale0"): device or resource busy
# while its container stays "running", so restart:unless-stopped never fires
# and `docker ps` shows it healthy.

# Order matters: a kernel-mode sidecar ALSO creates tailscale0, so checking
# for our own container first is what distinguishes "we did this" from
# "something else did".
tailnet_state() {
    if command -v docker >/dev/null 2>&1 &&
       docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'hummingbot-tailscale'; then
        echo sidecar; return
    fi

    # A userspace daemon has no interface at all, so the process check is not
    # redundant with the link check -- each catches a case the other misses.
    if pgrep -x tailscaled >/dev/null 2>&1; then
        echo native; return
    fi
    if ip link show tailscale0 >/dev/null 2>&1; then
        echo native; return
    fi
    # macOS names the device utunN, not tailscale0; fall back to asking the
    # CLI, which is the only portable signal there.
    if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
        echo native; return
    fi

    echo none
}

# Resolve the effective mode from .env plus what is actually on the host.
#
# TAILSCALE_MODE is authoritative when set. TAILSCALE_ENABLED remains the
# historical switch and keeps working: enabled-but-unspecified means "put the
# API on the tailnet, you pick how", and how depends on whether this host
# already has a daemon.
#
# Echoes: none | host | sidecar
tailnet_mode() {
    local declared="${TAILSCALE_MODE:-}"
    local enabled="${TAILSCALE_ENABLED:-false}"
    local state; state="$(tailnet_state)"

    case "$declared" in
        none|host|sidecar) echo "$declared"; return ;;
        "") : ;;
        *) echo "[ERROR] TAILSCALE_MODE must be none, host or sidecar (got: $declared)" >&2; return 1 ;;
    esac

    case "$enabled" in
        [Tt]rue|[Yy]es|1) : ;;
        *) echo none; return ;;
    esac

    # Enabled, mode unspecified. A host that already runs its own tailscaled
    # gets `host`: joining the tailnet a second time from the same machine
    # would contend for the same device for no gain.
    if [ "$state" = native ]; then echo host; else echo sidecar; fi
}

# Should the sidecar run in userspace (netstack) mode?
#
# This is the safety net that makes correctness independent of detection being
# right. Netstack creates no TUN device and touches no host routes or
# iptables, so it coexists with a kernel-mode daemon unconditionally --
# verified on a live tailnet: a userspace node registered alongside a
# kernel-mode one in the same netns and served a loopback-bound port to the
# other node, with a single tailscale0 between them.
#
# The cost is real but small: a userspace node cannot route for the host, only
# accept inbound. Serving port 8000 is exactly that, so nothing is lost here.
tailnet_needs_userspace() {
    [ "$(tailnet_state)" = native ]
}
