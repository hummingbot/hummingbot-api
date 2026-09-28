import logging
import os
from importlib import metadata
from typing import Optional

import psutil
from fastapi import APIRouter, Depends, HTTPException

from config import settings
from deps import get_docker_service, get_self_upgrade_service
from models import SelfUpgradeRequest
from services.docker_service import DockerService
from services.self_upgrade import SelfUpgradeRefused, SelfUpgradeService
from utils.compose import describe_container, own_container, pinning
from version import VERSION

logger = logging.getLogger(__name__)

router = APIRouter(tags=["System"], prefix="/system")

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

    container = own_container(client)
    if container is None:
        logger.warning("Could not identify the API's own container while reporting system info")
        return info

    try:
        info["container"] = describe_container(container)
    except Exception as e:
        logger.warning(f"Could not inspect the API's own container while reporting system info: {e}")
        return info

    info.update(pinning(info["container"]))
    return info


# ── Self-upgrade (FEAT-122) ──────────────────────────────────────────────────────
#
# Replacing this API's own container with the published image, so a remote server can
# be upgraded from a dashboard instead of over SSH. See services/self_upgrade.py for
# why a helper container does the recreate, and for the rule these three routes exist
# to enforce: anything not established is a refusal.


@router.get("/upgrade/preflight")
async def get_upgrade_preflight(upgrade_service: SelfUpgradeService = Depends(get_self_upgrade_service)):
    """
    Report whether this API can replace its own container, and what it would cost.

    Read-only and never raises: a daemon that cannot be reached, a container that cannot
    be placed and a registry that cannot be read are all reported as can_upgrade false
    with a blocked_reason, because the caller of a destructive action needs a reason far
    more than it needs a 500.

    Returns:
        Dictionary with image_ref, current_digest, available_digest, up_to_date, the
        pinning verdict (pinned / pinned_reason / override_file), the compose project,
        working dir and config files, running_executors and running_bots, and
        can_upgrade with its blocked_reason (None only when the upgrade may be started).
    """
    return await upgrade_service.preflight()


@router.post("/upgrade", status_code=202)
async def start_upgrade(
    request: Optional[SelfUpgradeRequest] = None,
    upgrade_service: SelfUpgradeService = Depends(get_self_upgrade_service),
):
    """
    Pull the published image and hand the container recreate to a helper container.

    The preflight is re-run here, so a server that became unupgradable since the caller
    last looked -- an executor started, another upgrade began, the daemon went away --
    is refused rather than upgraded on a stale reading.

    Args:
        request: acknowledge_executor_loss, the caller's consent to every running
            executor being closed as SYSTEM_CLEANUP. Required when any is running.

    Returns:
        202 with the run id and the initial phase. The API is replaced part-way through,
        so the result is read back from /system/upgrade/status after it restarts.

    Raises:
        HTTPException: 409 when the upgrade is refused. Nothing was pulled, and nothing
            on the server was changed.
    """
    # An absent body is a caller who acknowledged nothing, which is the refusing default.
    request = request or SelfUpgradeRequest()
    try:
        return await upgrade_service.start(acknowledge_executor_loss=request.acknowledge_executor_loss)
    except SelfUpgradeRefused as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/upgrade/status")
async def get_upgrade_status(upgrade_service: SelfUpgradeService = Depends(get_self_upgrade_service)):
    """
    Report the current or last self-upgrade.

    Phases: idle (there has been none), pulling, recreating (this API is about to be
    replaced, so the next answer comes from the new one), done, failed. After a
    successful recreate the record is the one this API collected from the helper
    container on boot, including its exit code and the tail of its output.

    Returns:
        Dictionary with run_id, phase, detail, previous_digest, new_digest, exit_code
        and log_tail.
    """
    return upgrade_service.status()
