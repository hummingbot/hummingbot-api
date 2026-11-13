"""
Tests for the ownership guard on ``POST /docker/stop-container/{name}`` and
``POST /docker/start-container/{name}``.

Before this guard, both endpoints took any container name on the host straight through to
the Docker daemon -- including the three other projects' containers this host also runs.
[[READ-115]] gave ``remove-container`` a real ownership check (a container is this API's
only if it owns the ``bots/instances/<name>`` directory it was created with); this extends
the same check, via the same ``DockerService.resolve_instance_dir``, to stop and start.

Run with: pytest test/test_stop_start_containers_are_api_managed.py -v
"""
import os

import pytest
from fastapi import HTTPException

from routers.docker import start_container, stop_container


class _StubContainer:
    def __init__(self):
        self.stopped = 0
        self.started = 0

    def stop(self):
        self.stopped += 1

    def start(self):
        self.started += 1


class _StubDocker:
    def __init__(self, container=None):
        self._container = container or _StubContainer()

    def stop_container(self, container_name):
        self._container.stop()
        return {"success": True, "message": f"Container {container_name} stopped."}

    def start_container(self, container_name):
        self._container.start()
        return {"success": True, "message": f"Container {container_name} started."}


@pytest.fixture
def instances_root(tmp_path, monkeypatch):
    """Run inside a scratch cwd so 'bots/instances' is a real, empty directory we control."""
    root = tmp_path / "bots" / "instances"
    root.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_name", ["pmm-1", "hummingbot-pmm-1", "my_bot.v2"])
async def test_api_managed_container_is_stopped_whatever_its_name(instances_root, bot_name):
    (instances_root / bot_name).mkdir()
    docker = _StubDocker()

    response = await stop_container(bot_name, docker)

    assert response["success"] is True
    assert docker._container.stopped == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_name", ["pmm-1", "hummingbot-pmm-1", "my_bot.v2"])
async def test_api_managed_container_is_started_whatever_its_name(instances_root, bot_name):
    (instances_root / bot_name).mkdir()
    docker = _StubDocker()

    response = await start_container(bot_name, docker)

    assert response["success"] is True
    assert docker._container.started == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("container_name", ["nginx", "hummingbot-postgres", "hummingbot-broker"])
async def test_unrelated_host_container_is_refused_on_stop(instances_root, container_name):
    docker = _StubDocker()

    with pytest.raises(HTTPException) as exc:
        await stop_container(container_name, docker)

    assert exc.value.status_code == 400
    assert "managed by this API" in exc.value.detail
    assert docker._container.stopped == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("container_name", ["nginx", "hummingbot-postgres", "hummingbot-broker"])
async def test_unrelated_host_container_is_refused_on_start(instances_root, container_name):
    docker = _StubDocker()

    with pytest.raises(HTTPException) as exc:
        await start_container(container_name, docker)

    assert exc.value.status_code == 400
    assert "managed by this API" in exc.value.detail
    assert docker._container.started == 0


@pytest.mark.asyncio
async def test_name_escaping_the_instances_directory_is_refused_on_stop(instances_root):
    docker = _StubDocker()

    with pytest.raises(HTTPException) as exc:
        await stop_container(os.path.join("..", "credentials"), docker)

    assert exc.value.status_code == 400
    assert docker._container.stopped == 0


@pytest.mark.asyncio
async def test_name_escaping_the_instances_directory_is_refused_on_start(instances_root):
    docker = _StubDocker()

    with pytest.raises(HTTPException) as exc:
        await start_container(os.path.join("..", "credentials"), docker)

    assert exc.value.status_code == 400
    assert docker._container.started == 0
