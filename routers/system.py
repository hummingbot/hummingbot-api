import os

import psutil

from fastapi import APIRouter

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
