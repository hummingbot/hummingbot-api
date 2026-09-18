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

CONTAINER_ID = "3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a"  # noqa: mock  (a Docker container id, not a key)
WORKING_DIR = "/home/hbot/hummingbot-api"


def _container(
    digests=("hummingbot/hummingbot-api@sha256:abc123",),
    config_files=f"{WORKING_DIR}/docker-compose.yml",
    image="hummingbot/hummingbot-api:latest",
    name="hummingbot-api",
    project="hummingbot-api",
):
    """A docker-py Container as this route reads it."""
    return SimpleNamespace(
        id=CONTAINER_ID,
        name=name,
        labels={
            "com.docker.compose.project": project,
            "com.docker.compose.project.working_dir": WORKING_DIR,
            "com.docker.compose.project.config_files": config_files,
            "com.docker.compose.service": "hummingbot-api",
        },
        attrs={"Config": {"Image": image}},
        image=SimpleNamespace(
            id="sha256:deadbeef",
            tags=[image],
            attrs={"RepoDigests": list(digests)},
        ),
    )


class _Containers:
    def __init__(self, container, found_by_hostname=True, others=(), found_by_id=None):
        self.container = container
        self.found_by_hostname = found_by_hostname
        self.found_by_id = found_by_id
        self.others = list(others)
        self.list_filters = None

    def get(self, name):
        if self.found_by_hostname or (self.found_by_id and name == self.found_by_id):
            return self.container
        raise NotFound(f"no such container: {name}")

    def list(self, filters=None):
        self.list_filters = filters
        return ([self.container] if self.container is not None else []) + self.others


class _Client:
    def __init__(self, container, found_by_hostname=True, reachable=True, others=(), found_by_id=None):
        self.containers = _Containers(container, found_by_hostname, others, found_by_id)
        self.reachable = reachable

    def ping(self):
        if not self.reachable:
            raise DockerException("Error while fetching server API version")
        return True


@pytest.fixture(autouse=True)
def inside_a_container(monkeypatch):
    """Run as the containerised API by default, with no container id in the mount table."""
    from utils import compose

    monkeypatch.setattr(compose, "_container_id_from_mountinfo", lambda: None)
    monkeypatch.setattr(compose, "_in_container", lambda: True)
    return monkeypatch


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


def test_locally_built_image_on_the_containerd_store_is_pinned(make_client):
    # The containerd image store gives a local build a RepoDigest of its own; it is not a
    # digest in the published repository, so it does not make the image the published one.
    sha = "sha256:f84fd1ab6802c4434a8ea709c63a7a99c30b4aaa80e828fc508d1ebb2e65f806"  # noqa: mock
    container = _container(digests=(f"hbapi@{sha}",), image="hbapi:pr235")
    body = make_client(_Client(container)).get("/system/info").json()

    assert body["container"]["digest"] is None
    assert body["pinned"] is True
    assert "locally" in body["pinned_reason"]


def test_a_fully_qualified_published_digest_is_the_published_image(make_client):
    container = _container(digests=("docker.io/hummingbot/hummingbot-api@sha256:abc123",))
    body = make_client(_Client(container)).get("/system/info").json()

    assert body["container"]["digest"] == "docker.io/hummingbot/hummingbot-api@sha256:abc123"
    assert body["pinned"] is False


def test_a_version_tag_in_the_main_compose_file_is_pinned(make_client):
    # Pulled from the registry, so it has a published digest -- but upgrading it would
    # pull the same tag back, not latest.
    container = _container(image="hummingbot/hummingbot-api:1.0.1")
    body = make_client(_Client(container)).get("/system/info").json()

    assert body["pinned"] is True
    assert "1.0.1" in body["pinned_reason"]
    assert body["override_file"] is None


def test_an_image_pinned_by_digest_is_pinned(make_client):
    container = _container(image="hummingbot/hummingbot-api@sha256:abc123")
    assert make_client(_Client(container)).get("/system/info").json()["pinned"] is True


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


def test_finds_itself_by_container_id_when_the_hostname_is_overridden(make_client, inside_a_container):
    from utils import compose

    inside_a_container.setattr(compose, "_container_id_from_mountinfo", lambda: CONTAINER_ID)
    decoy = _container(name="decoy-api", project="some-other-stack", image="alpine:latest")
    client = _Client(_container(), found_by_hostname=False, found_by_id=CONTAINER_ID, others=[decoy])
    body = make_client(client).get("/system/info").json()

    assert body["container"]["name"] == "hummingbot-api"
    assert client.containers.list_filters is None  # never needed the label fallback


def test_two_candidates_for_the_service_label_report_no_container(make_client):
    # Another stack on the same host runs a hummingbot-api service too. Picking one would
    # report its image and its pinning verdict as this server's.
    decoy = _container(name="decoy-api", project="some-other-stack", image="alpine:latest")
    body = make_client(_Client(_container(), found_by_hostname=False, others=[decoy])).get("/system/info").json()

    assert body["docker_available"] is True
    assert body["container"] is None
    assert body["pinned"] is None


def test_running_from_source_does_not_claim_a_container(make_client, inside_a_container):
    # From source this process is no container at all; the containerised API on the same
    # host carries the service label, but it is not us.
    from utils import compose

    inside_a_container.setattr(compose, "_in_container", lambda: False)
    client = _Client(_container(), found_by_hostname=False)
    body = make_client(client).get("/system/info").json()

    assert body["container"] is None
    assert body["pinned"] is None
    assert client.containers.list_filters is None


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


def test_reports_the_market_data_tunables(make_client):
    # The dashboard shows these read-only: they come from the server's .env, which is not
    # mounted into the container, so the API can report them but never rewrite them.
    from config import settings

    body = make_client(_Client(_container())).get("/system/info").json()

    assert body["market_data"] == settings.market_data.model_dump()
    assert body["market_data"]["ticker_update_interval"] == settings.market_data.ticker_update_interval


def test_market_data_tunables_carry_no_secret(make_client):
    # MarketDataSettings is intervals and timeouts by construction. Pinned so a later
    # field that does not belong in an unauthenticated-looking payload fails here first.
    body = make_client(_Client(_container())).get("/system/info").json()

    assert body["market_data"]
    assert all(isinstance(v, (int, float)) for v in body["market_data"].values())
    assert not any(
        word in key.lower()
        for key in body["market_data"]
        for word in ("password", "secret", "token", "key", "user")
    )


def test_market_data_is_reported_without_docker(make_client):
    # The versions and the tunables are the part that works from source too.
    body = make_client(_Client(_container(), reachable=False)).get("/system/info").json()

    assert body["docker_available"] is False
    assert body["market_data"]
