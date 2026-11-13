"""
Tests for ``DockerService.clean_exited_containers``.

It used to be a bare ``containers.prune()`` -- daemon-wide, removing every exited
container on the host, not just this API's own. This host runs other projects'
containers on the same shared daemon, so a stray operator call during disk pressure
could remove one of theirs. The fix: list exited containers and remove only the ones
that own a ``bots/instances/<name>`` directory, the same ownership marker
``remove-container`` already checks.

Run with: pytest test/test_clean_exited_containers_is_api_managed.py -v
"""
import pytest
from docker.errors import DockerException

from services.docker_service import DockerService


class _ExitedContainer:
    def __init__(self, name):
        self.name = name
        self.removed = 0

    def remove(self):
        self.removed += 1


class _Containers:
    def __init__(self, exited=None, error=None):
        self._exited = exited or []
        self.error = error
        self.prune_called = False

    def list(self, **kwargs):
        if self.error:
            raise self.error
        return self._exited

    def prune(self):
        # If this is ever called again, the daemon-wide hazard is back.
        self.prune_called = True


def _service(exited=None, error=None):
    service = DockerService.__new__(DockerService)
    service.client = type("_Client", (), {})()
    service.client.containers = _Containers(exited=exited, error=error)
    return service


@pytest.fixture
def instances_root(tmp_path, monkeypatch):
    root = tmp_path / "bots" / "instances"
    root.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return root


def test_an_api_managed_exited_container_is_removed(instances_root):
    (instances_root / "pmm-1").mkdir()
    owned = _ExitedContainer("pmm-1")
    service = _service(exited=[owned])

    result = service.clean_exited_containers()

    assert result["success"] is True
    assert owned.removed == 1
    assert service.client.containers.prune_called is False


def test_an_unrelated_exited_container_is_left_in_place(instances_root):
    unrelated = _ExitedContainer("respawn-chat-db-1")
    service = _service(exited=[unrelated])

    result = service.clean_exited_containers()

    assert result["success"] is True
    assert unrelated.removed == 0
    assert service.client.containers.prune_called is False


def test_only_the_owned_container_among_several_is_removed(instances_root):
    (instances_root / "pmm-1").mkdir()
    owned = _ExitedContainer("pmm-1")
    unrelated = _ExitedContainer("other-project-mongodb")
    service = _service(exited=[owned, unrelated])

    result = service.clean_exited_containers()

    assert result["success"] is True
    assert owned.removed == 1
    assert unrelated.removed == 0


def test_daemon_failure_is_reported_in_band(instances_root):
    service = _service(error=DockerException("boom"))

    result = service.clean_exited_containers()

    assert result["success"] is False
    assert result["error"] == "docker_error"
