"""Shared mTLS certificate management for the Hummingbot Gateway (SEC-048).

A single self-signed CA plus server/client cert set secures the Gateway transport.
Every consumer shares one cert set, with all private keys encrypted using a single
secret (``settings.security.config_password``):

- the Gateway server container (decrypts ``server_key.pem`` with ``GATEWAY_PASSPHRASE``),
- this API's two Gateway clients (the custom :class:`GatewayClient` and hummingbot's
  in-process ``GatewayHttpClient``), and
- deployed Hummingbot instances (decrypt with ``CONFIG_PASSWORD``).

Generation is idempotent: an existing CA is reused so already-deployed instances keep
working across ``start``/``restart``.
"""
import os
import shutil
import ssl
from typing import Optional

from hummingbot import root_path
from hummingbot.core.utils.ssl_cert import create_self_sign_certs

# Full set written by create_self_sign_certs that makes up the mTLS chain.
SERVER_CERT_FILES = (
    "ca_cert.pem", "ca_key.pem",
    "server_cert.pem", "server_key.pem",
    "client_cert.pem", "client_key.pem",
)
# Subset a *client* needs to trust the server and present its own cert.
CLIENT_CERT_FILES = ("ca_cert.pem", "client_cert.pem", "client_key.pem")

GATEWAY_DIR = "gateway-files"
CERTS_SUBDIR = "certs"


def _bots_path() -> str:
    # Mirrors GatewayService/DockerService: BOTS_PATH (host path) when containerized.
    return os.environ.get("BOTS_PATH", os.getcwd())


def gateway_certs_dir() -> str:
    """Canonical host directory holding the shared mTLS cert set."""
    return os.path.join(_bots_path(), GATEWAY_DIR, CERTS_SUBDIR)


def certs_present(certs_dir: Optional[str] = None) -> bool:
    """True only when the full server+client set exists (a partial set is treated as absent)."""
    certs_dir = certs_dir or gateway_certs_dir()
    return all(os.path.exists(os.path.join(certs_dir, name)) for name in SERVER_CERT_FILES)


def ensure_gateway_certs(passphrase: str, certs_dir: Optional[str] = None) -> str:
    """Generate the shared mTLS cert set if absent, then mirror client certs to root_path().

    Idempotent: when the set already exists the existing CA is reused untouched, so
    instances deployed before/after the Gateway starts all share one CA. Returns the
    canonical certs directory.
    """
    certs_dir = certs_dir or gateway_certs_dir()
    os.makedirs(certs_dir, exist_ok=True)
    if not certs_present(certs_dir):
        create_self_sign_certs(passphrase, certs_dir)
    sync_client_certs_to_root(certs_dir)
    return certs_dir


def sync_client_certs_to_root(certs_dir: Optional[str] = None) -> None:
    """Mirror the client cert set into ``root_path()/certs``.

    hummingbot's in-process ``GatewayHttpClient`` reads certs only from
    ``root_path()/certs``, so the same CA must be available there for its SSL calls to
    succeed. No-op when the source set is absent.
    """
    certs_dir = certs_dir or gateway_certs_dir()
    if not certs_present(certs_dir):
        return
    target = os.path.join(str(root_path()), "certs")
    os.makedirs(target, exist_ok=True)
    for name in CLIENT_CERT_FILES:
        src = os.path.join(certs_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target, name))


def build_client_ssl_context(passphrase: str, certs_dir: Optional[str] = None) -> ssl.SSLContext:
    """Build an SSLContext that trusts the shared CA and presents the client cert.

    Raises ``FileNotFoundError`` if the cert set has not been generated yet.
    """
    certs_dir = certs_dir or gateway_certs_dir()
    ca_file = os.path.join(certs_dir, "ca_cert.pem")
    if not os.path.exists(ca_file):
        raise FileNotFoundError(
            f"Gateway client certs not found in {certs_dir}; start the Gateway to generate them."
        )
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.load_cert_chain(
        certfile=os.path.join(certs_dir, "client_cert.pem"),
        keyfile=os.path.join(certs_dir, "client_key.pem"),
        password=passphrase,
    )
    return ctx
