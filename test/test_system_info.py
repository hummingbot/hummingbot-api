"""
Tests for the /system/info endpoint.

The dashboard needs to tell which hummingbot-api and hummingbot version a server runs,
and whether its image is the published one or something pinned on the box. That answer
comes from the API's own container over the mounted docker.sock, which is exactly the
thing that can be missing -- so pinned here: the happy path reports the digest and the
compose labels, a locally built image and a compose override file each read as pinned,
and a daemon that cannot be reached still answers with both versions.

Run with: pytest test/test_system_info.py -v
"""
from types import SimpleNamespace

import pytest
from docker.errors import DockerException, NotFound
from fastapi import FastAPI
from fastapi.testclient import TestClient

from version import VERSION

CONTAINER_ID = "3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a"
WORKING_DIR = "/home/hbot/hummingbot-api"


def _container(digests=("hummingbot/hummingbot-api@sha256:abc123",), config_files=f"{WORKING_DIR}/docker-compose.yml"):
    """A docker-py Container as this route reads it."""
    return SimpleNamespace(
        id=CONTAINER_ID,
        name="hummingbot-api",
        labels={
            "com.docker.compose.project": "hummingbot-api",
            "com.docker.compose.project.working_dir": WORKING_DIR,
            "com.docker.compose.project.config_files": config_files,
            "com.docker.compose.service": "hummingbot-api",
        },
        attrs={"Config": {"Image": "hummingbot/hummingbot-api:latest"}},
        image=SimpleNamespace(
            id="sha256:deadbeef",
            tags=["hummingbot/hummingbot-api:latest"],
            attrs={"RepoDigests": list(digests)},
        ),
    )


class _Containers:
    def __init__(self, container, found_by_hostname=True):
        self.container = container
        self.found_by_hostname = found_by_hostname
        self.list_filters = None

    def get(self, name):
        if not self.found_by_hostname:
            raise NotFound(f"no such container: {name}")
        return self.container

    def list(self, filters=None):
        self.list_filters = filters
        return [self.container] if self.container is not None else []


class _Client:
    def __init__(self, container, found_by_hostname=True, reachable=True):
        self.containers = _Containers(container, found_by_hostname)
        self.reachable = reachable

    def ping(self):
        if not self.reachable:
            raise DockerException("Error while fetching server API version")
        return True


@pytest.fixture
def make_client():
    """Build a TestClient whose /system/info talks to the given fake docker client."""
    from deps import get_docker_service
    from routers import system

    def _make(docker_client):
        app = FastAPI()
        app.include_router(system.router)
        app.dependency_overrides[get_docker_service] = lambda: SimpleNamespace(client=docker_client)
        return TestClient(app)

    return _make


def test_reports_versions_and_container(make_client):
    body = make_client(_Client(_container())).get("/system/info").json()

    assert body["api_version"] == VERSION
    assert body["hummingbot_version"]  # the library is installed in the test environment
    assert body["docker_available"] is True
    assert body["container"] == {
        "id": CONTAINER_ID,
        "name": "hummingbot-api",
        "image": "hummingbot/hummingbot-api:latest",
        "image_id": "sha256:deadbeef",
        "digest": "hummingbot/hummingbot-api@sha256:abc123",
        "compose_project": "hummingbot-api",
        "compose_working_dir": WORKING_DIR,
        "compose_config_files": f"{WORKING_DIR}/docker-compose.yml",
    }
    assert body["pinned"] is False
    assert body["pinned_reason"] is None
    assert body["override_file"] is None


def test_locally_built_image_is_pinned(make_client):
    body = make_client(_Client(_container(digests=()))).get("/system/info").json()

    assert body["container"]["digest"] is None
    assert body["pinned"] is True
    assert "locally" in body["pinned_reason"]
    assert body["override_file"] is None


def test_override_file_is_pinned_and_named(make_client):
    container = _container(
        config_files=f"{WORKING_DIR}/docker-compose.yml,{WORKING_DIR}/docker-compose.override.yml",
    )
    body = make_client(_Client(container)).get("/system/info").json()

    assert body["pinned"] is True
    assert body["override_file"] == "docker-compose.override.yml"
    assert "docker-compose.override.yml" in body["pinned_reason"]


def test_extra_overlay_that_is_not_an_override_is_not_pinned(make_client):
    # The Tailscale overlay repoints networking, not the image.
    container = _container(
        config_files=f"{WORKING_DIR}/docker-compose.yml,{WORKING_DIR}/docker-compose.tailscale.yml",
    )
    assert make_client(_Client(container)).get("/system/info").json()["pinned"] is False


def test_falls_back_to_the_compose_service_label(make_client):
    client = _Client(_container(), found_by_hostname=False)
    body = make_client(client).get("/system/info").json()

    assert client.containers.list_filters == {"label": "com.docker.compose.service=hummingbot-api"}
    assert body["container"]["name"] == "hummingbot-api"


def test_docker_unavailable_still_reports_versions(make_client):
    body = make_client(_Client(_container(), reachable=False)).get("/system/info").json()

    assert body["docker_available"] is False
    assert body["container"] is None
    assert body["pinned"] is None
    assert body["pinned_reason"] is None
    assert body["api_version"] == VERSION
    assert body["hummingbot_version"]


def test_container_not_identified_reports_no_container(make_client):
    client = _Client(None, found_by_hostname=False)
    body = make_client(client).get("/system/info").json()

    assert body["docker_available"] is True
    assert body["container"] is None
    assert body["pinned"] is None
    assert body["api_version"] == VERSION


def test_docker_service_without_a_client_never_raises():
    """DockerService leaves `client` unset when the daemon was absent at startup."""
    from deps import get_docker_service
    from routers import system

    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_docker_service] = lambda: SimpleNamespace()

    body = TestClient(app).get("/system/info").json()
    assert body["docker_available"] is False
    assert body["container"] is None
    assert body["api_version"] == VERSION
