import os
from typing import Dict, List

from fastapi import APIRouter

from utils.file_system import fs_util

router = APIRouter(tags=["Storage"], prefix="/storage")

BOTS_BASE = fs_util.get_base_path()
TRACKED_DIRS = ["archived", "instances", "conf"]


def _get_dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def _get_subdirectory_breakdown(path: str) -> List[Dict]:
    if not os.path.isdir(path):
        return []
    items = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            size = _get_dir_size(full)
            items.append({
                "name": name,
                "size_bytes": size,
                "size_human": _format_size(size),
            })
    items.sort(key=lambda x: x["size_bytes"], reverse=True)
    return items


@router.get("/")
async def get_storage_overview():
    """Get disk usage overview for bots directories (archived, instances, conf)."""
    result = {}
    total_bytes = 0

    for dirname in TRACKED_DIRS:
        dir_path = os.path.join(BOTS_BASE, dirname)
        if not os.path.isdir(dir_path):
            result[dirname] = {
                "size_bytes": 0,
                "size_human": "0.00 B",
                "item_count": 0,
                "items": [],
            }
            continue

        size = _get_dir_size(dir_path)
        total_bytes += size
        items = _get_subdirectory_breakdown(dir_path)

        result[dirname] = {
            "size_bytes": size,
            "size_human": _format_size(size),
            "item_count": len(items),
            "items": items,
        }

    return {
        "total_size_bytes": total_bytes,
        "total_size_human": _format_size(total_bytes),
        "directories": result,
    }
