import logging
import os
import socket
from importlib import metadata

import psutil
from fastapi import APIRouter, Depends

from config import settings
from deps import get_docker_service
from services.docker_service import DockerService
from version import VERSION

logger = logging.getLogger(__name__)

router = APIRouter(tags=["System"], prefix="/system")

# Compose service this API is deployed as, used to find our own container when the
# hostname lookup fails (see _own_container).
COMPOSE_SERVICE = "hummingbot-api"

# Compose stamps these on every container it creates; they say which project the API
# belongs to and which files were used to bring it up.
LABEL_PROJECT = "com.docker.compose.project"
LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"

# When running inside Docker, mount the host's /proc and root filesystem into the
# container and point these env vars at the mount locations so psutil reports the
# HOST machine's metrics rather than the container's. They default to the local
# paths so the route also works when running outside a container.
#   HOST_PROC      -> e.g. /host/proc   (host /proc mounted read-only)
#   HOST_DISK_PATH -> e.g. /host/root   (host / mounted read-only)
HOST_PROC = os.environ.get("HOST_PROC", "/proc")
HOST_DISK_PATH = os.environ.get("HOST_DISK_PATH", "/")

# psutil reads CPU, memory and load-average data from this path. Setting it to the
# mounted host /proc makes those metrics reflect the host instead of the container.
psutil.PROCFS_PATH = HOST_PROC

# Prime cpu_percent so the first call returns a meaningful value rather than 0.0
# (psutil measures CPU utilization between successive calls).
psutil.cpu_percent(interval=None)


@router.get("/resources")
async def get_system_resources():
    """
    Get host machine CPU, RAM, and disk usage.

    Returns:
        Dictionary with current CPU, memory, and disk utilization for the host.
    """
    try:
        load_avg = psutil.getloadavg()
    except (OSError, AttributeError):
        # getloadavg is not available on every platform.
        load_avg = (0.0, 0.0, 0.0)

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(HOST_DISK_PATH)

    return {
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "count_logical": psutil.cpu_count(logical=True),
            "count_physical": psutil.cpu_count(logical=False),
            "load_avg_1m": load_avg[0],
            "load_avg_5m": load_avg[1],
            "load_avg_15m": load_avg[2],
        },
        "memory": {
            "total": vm.total,
            "available": vm.available,
            "used": vm.used,
            "percent": vm.percent,
        },
        "disk": {
            # Display label only; the stats are for the filesystem containing
            # HOST_DISK_PATH (the host root partition).
            "mountpoint": "host root",
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
    }


def _hummingbot_version():
    """The version of the hummingbot library this API runs against, or None if absent."""
    try:
        return metadata.version("hummingbot")
    except metadata.PackageNotFoundError:
        return None


def _own_container(client):
    """Find the container this API process runs in.

    Docker sets a container's hostname to its own short id, which is how a process
    identifies itself from the inside. That fails when the deployment overrides
    `hostname:` or when the API runs from source on the host, so fall back to the one
    container carrying this project's compose service label.

    Returns:
        A docker-py Container, or None when this process cannot be placed.
    """
    try:
        return client.containers.get(socket.gethostname())
    except Exception:
        pass
    try:
        matches = client.containers.list(filters={"label": f"com.docker.compose.service={COMPOSE_SERVICE}"})
    except Exception:
        return None
    return matches[0] if matches else None


def _describe_container(container):
    """Identity, image and compose provenance of a container.

    Returns:
        Dictionary with the container's id and name, the image reference it was started
        from, the image id, its registry digest (None for a locally built image) and the
        three compose labels that say where the deployment lives.
    """
    labels = container.labels or {}
    image = container.image
    # A locally built image has no RepoDigests: nothing published it, so there is no
    # content address to compare against the tag it carries.
    digests = (image.attrs.get("RepoDigests") or []) if image is not None else []
    return {
        "id": container.id,
        "name": container.name,
        "image": container.attrs.get("Config", {}).get("Image"),
        "image_id": image.id if image is not None else None,
        "digest": digests[0] if digests else None,
        "compose_project": labels.get(LABEL_PROJECT),
        "compose_working_dir": labels.get(LABEL_WORKING_DIR),
        "compose_config_files": labels.get(LABEL_CONFIG_FILES),
    }


def _pinning(container):
    """Whether this deployment runs something other than the published image.

    Two things make an image not the one `hummingbot/hummingbot-api:latest` currently
    resolves to: it was built on the box (no registry digest), or compose was brought up
    with an override file that can repoint `image:` at a pinned tag. Only files whose
    name contains "override" count -- `docker-compose.tailscale.yml` is an overlay for
    networking, not for the image.

    Returns:
        Dictionary with pinned, a short pinned_reason and the override file name (None
        when that is not the reason).
    """
    files = [f.strip() for f in (container.get("compose_config_files") or "").split(",") if f.strip()]
    override_file = next((os.path.basename(f) for f in files if "override" in os.path.basename(f).lower()), None)

    reasons = []
    if not container.get("digest"):
        reasons.append("image was built locally (no registry digest)")
    if override_file:
        reasons.append(f"compose override file {override_file} can pin the image")

    return {
        "pinned": bool(reasons),
        "pinned_reason": "; ".join(reasons) or None,
        "override_file": override_file,
    }


@router.get("/info")
async def get_system_info(docker_service: DockerService = Depends(get_docker_service)):
    """
    Get the versions this API server runs and how its own container was deployed.

    Read-only, and never raises: when docker.sock is not mounted, the daemon is
    unreachable or this process cannot find its own container, the version fields are
    still reported and the container block is None.

    Returns:
        Dictionary with the API version, the hummingbot library version, the market-data
        tunables this process is running with, whether the Docker daemon could be
        reached, the API's own container (id, name, image, digest and compose labels),
        and whether that image is pinned, with a short reason and the override file name
        when it is.
    """
    info = {
        "api_version": VERSION,
        "hummingbot_version": _hummingbot_version(),
        # The MARKET_DATA_* knobs as this process resolved them. Reported rather than
        # editable: they come from the env_file, which is not mounted into the
        # container, so the API cannot write its own .env -- a dashboard shows them and
        # says where to change them. Non-secret by construction (MarketDataSettings
        # holds only intervals and timeouts).
        "market_data": settings.market_data.model_dump(),
        "docker_available": False,
        "container": None,
        # None rather than False: without the daemon we have not established that the
        # image is unpinned, only that we cannot tell.
        "pinned": None,
        "pinned_reason": None,
        "override_file": None,
    }

    # DockerService leaves `client` unset when docker.from_env() failed at startup.
    client = getattr(docker_service, "client", None)
    if client is None:
        return info
    try:
        client.ping()
    except Exception as e:
        logger.warning(f"Docker daemon unreachable while reporting system info: {e}")
        return info
    info["docker_available"] = True

    container = _own_container(client)
    if container is None:
        logger.warning("Could not identify the API's own container while reporting system info")
        return info

    try:
        info["container"] = _describe_container(container)
    except Exception as e:
        logger.warning(f"Could not inspect the API's own container while reporting system info: {e}")
        return info

    info.update(_pinning(info["container"]))
    return info
