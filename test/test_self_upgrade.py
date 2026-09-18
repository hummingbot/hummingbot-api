"""The API replacing its own container (FEAT-122).

The failure mode of this feature is a trading API server that does not come back, so
almost everything here is a refusal. `preflight` starts blocked and only clears that once
every fact it needs is established, and `start` re-runs it, so the tests worth having are
the ones that prove nothing happens:

* an unreachable daemon, a container it cannot place, a stack with no compose labels
* a *pinned* image -- built on the box, or an override file that can repoint `image:` --
  which is the moneymaker case and must name the override file
* a registry it cannot read, which is "no evidence a newer image exists", not "go ahead"
* an executor count it cannot take, because a restart closes every running executor
* running executors without the caller acknowledging that loss
* a pull that fails, which must leave the server untouched and never start the helper

The happy path is here too, and what it asserts is the exact `docker compose` the helper
is given: the project, every config file in the order compose was given them, `--no-deps`
so postgres/emqx/bots are not touched, and the working dir bound at the *same* host path
so `./bots`, `.env` and the override file resolve to what the operator actually has.

Run with: pytest test/test_self_upgrade.py -v
"""
import threading
from types import SimpleNamespace

import pytest
from docker.errors import DockerException, NotFound
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.self_upgrade import (
    HELPER_IMAGE,
    HELPER_LABEL,
    HELPER_PREVIOUS_DIGEST_LABEL,
    SERVICE_NAME,
    SelfUpgradeRefused,
    SelfUpgradeService,
)

CONTAINER_ID = "3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a"  # noqa: mock  (a Docker container id, not a key)
WORKING_DIR = "/home/hbot/hummingbot-api"
IMAGE_REF = "hummingbot/hummingbot-api:latest"
CURRENT_SHA = "sha256:1111111111111111111111111111111111111111111111111111111111111111"  # noqa: mock
NEWER_SHA = "sha256:2222222222222222222222222222222222222222222222222222222222222222"  # noqa: mock
COMPOSE_FILES = f"{WORKING_DIR}/docker-compose.yml,{WORKING_DIR}/docker-compose.tailscale.yml"


# ── Doubles ──────────────────────────────────────────────────────────────────────


def _container(digests=(f"hummingbot/hummingbot-api@{CURRENT_SHA}",), config_files=COMPOSE_FILES):
    """A docker-py Container as utils/compose.py reads it."""
    labels = {
        "com.docker.compose.project": "hummingbot-api",
        "com.docker.compose.project.working_dir": WORKING_DIR,
        "com.docker.compose.service": SERVICE_NAME,
    }
    if config_files is not None:
        labels["com.docker.compose.project.config_files"] = config_files
    return SimpleNamespace(
        id=CONTAINER_ID,
        name="hummingbot-api",
        labels=labels,
        attrs={"Config": {"Image": IMAGE_REF}},
        image=SimpleNamespace(
            id="sha256:deadbeef",  # noqa: mock
            tags=[IMAGE_REF],
            attrs={"RepoDigests": list(digests)},
        ),
    )


def _helper(status="exited", exit_code=0, run_id="run123", logs=b"Container hummingbot-api Started\n"):
    removed = []
    return SimpleNamespace(
        name=f"helper-{run_id}",
        status=status,
        labels={HELPER_LABEL: run_id, HELPER_PREVIOUS_DIGEST_LABEL: f"hummingbot/hummingbot-api@{CURRENT_SHA}"},
        attrs={"State": {"ExitCode": exit_code}},
        logs=lambda tail=None: logs,
        remove=lambda force=False: removed.append(force),
        removed=removed,
    )


class _Containers:
    def __init__(self, container, helpers=()):
        self.container = container
        self.helpers = list(helpers)
        self.runs = []

    def get(self, name):
        if self.container is None:
            raise NotFound(f"no such container: {name}")
        return self.container

    def list(self, all=False, filters=None):
        label = (filters or {}).get("label", "")
        if label.startswith(HELPER_LABEL):
            return [h for h in self.helpers if not getattr(h, "removed", None)]
        # The compose-service fallback own_container() uses.
        return [self.container] if self.container is not None else []

    def run(self, image, **kwargs):
        self.runs.append({"image": image, **kwargs})
        return SimpleNamespace(name="helper-new")


class _Images:
    def __init__(self, registry_sha=NEWER_SHA):
        self.registry_sha = registry_sha

    def get_registry_data(self, ref):
        if self.registry_sha is None:
            raise DockerException(f"error while looking up {ref}")
        return SimpleNamespace(id=self.registry_sha)


class _Client:
    def __init__(self, container=None, helpers=(), registry_sha=NEWER_SHA, reachable=True):
        self.containers = _Containers(container, helpers)
        self.images = _Images(registry_sha)
        self.reachable = reachable

    def ping(self):
        if not self.reachable:
            raise DockerException("Error while fetching server API version")
        return True


class _DockerService:
    """Just the two things SelfUpgradeService uses off the real one."""

    def __init__(self, client, pull_fails=()):
        self.client = client
        self.pull_fails = set(pull_fails)
        self.pulled = []

    def pull_image_sync(self, image_name):
        self.pulled.append(image_name)
        if image_name in self.pull_fails:
            return {"success": False, "error": f"500 Server Error for http+docker://localhost/v1.55/images/create: {image_name}"}
        return {"success": True, "image": image_name}


class _Executors:
    def __init__(self, active=0, raises=False):
        self.active = active
        self.raises = raises

    def get_summary(self):
        if self.raises:
            raise RuntimeError("executor service is not up")
        return {"total_active": self.active}


class _Bots:
    def __init__(self, names=("bot-a", "bot-b")):
        self.names = list(names)

    async def get_active_containers(self):
        return list(self.names)


def _service(client=None, pull_fails=(), active=0, executor_raises=False, bots=None):
    if client is None:
        client = _Client(_container())
    return SelfUpgradeService(
        docker_service=_DockerService(client, pull_fails),
        executor_service=_Executors(active, executor_raises),
        bots_orchestrator=_Bots() if bots is None else bots,
    )


@pytest.fixture
def run_threads_inline(monkeypatch):
    """Run the upgrade's background thread on the calling thread, so tests are ordered."""
    started = []

    class _Inline:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self.target, self.args = target, args

        def start(self):
            started.append(self)
            self.target(*self.args)

    monkeypatch.setattr(threading, "Thread", _Inline)
    return started


# ── Preflight refuses ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refuses_when_the_docker_daemon_is_unreachable():
    info = await _service(_Client(_container(), reachable=False)).preflight()

    assert info["can_upgrade"] is False
    assert "docker.sock" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_when_the_docker_client_was_never_built():
    service = SelfUpgradeService(docker_service=SimpleNamespace(), executor_service=_Executors())

    info = await service.preflight()

    assert info["can_upgrade"] is False
    assert "Docker daemon is not reachable" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_when_it_cannot_identify_its_own_container():
    info = await _service(_Client(container=None)).preflight()

    assert info["can_upgrade"] is False
    assert "could not identify its own container" in info["blocked_reason"]
    assert "SSH" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_when_there_are_no_compose_labels():
    """Not a compose deployment: there is no `docker compose up` to replay."""
    info = await _service(_Client(_container(config_files=None))).preflight()

    assert info["can_upgrade"] is False
    assert info["compose"] is None
    assert "not started by Docker Compose" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_a_pinned_image_and_names_the_override_file():
    """The moneymaker case: an override file can repoint `image:` at a pinned tag."""
    files = f"{WORKING_DIR}/docker-compose.yml,{WORKING_DIR}/docker-compose.override.yml"

    info = await _service(_Client(_container(config_files=files))).preflight()

    assert info["can_upgrade"] is False
    assert info["pinned"] is True
    assert info["override_file"] == "docker-compose.override.yml"
    assert "docker-compose.override.yml" in info["blocked_reason"]
    assert "SSH" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_a_locally_built_image():
    """No RepoDigests: nothing published this image, so the published one is not it."""
    info = await _service(_Client(_container(digests=()))).preflight()

    assert info["can_upgrade"] is False
    assert info["pinned"] is True
    assert "built locally" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_when_the_registry_digest_cannot_be_read():
    """No evidence a newer image exists is not the same as "go ahead"."""
    service = _service(_Client(_container(), registry_sha=None))

    info = await service.preflight()

    assert info["can_upgrade"] is False
    assert info["available_digest"] is None
    assert "no evidence that a newer image exists" in info["blocked_reason"]
    assert service.docker_service.pulled == []


@pytest.mark.asyncio
async def test_refuses_when_already_running_the_published_image():
    info = await _service(_Client(_container(), registry_sha=CURRENT_SHA)).preflight()

    assert info["can_upgrade"] is False
    assert info["up_to_date"] is True
    assert "Already running" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_when_the_executor_count_cannot_be_taken():
    """A restart closes every running executor; not knowing how many is not "none"."""
    info = await _service(executor_raises=True).preflight()

    assert info["can_upgrade"] is False
    assert info["running_executors"] is None
    assert "Refusing rather than guessing" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_while_a_helper_container_is_present():
    info = await _service(_Client(_container(), helpers=[_helper(status="running")])).preflight()

    assert info["can_upgrade"] is False
    assert "still running" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_refuses_when_it_cannot_check_for_an_existing_helper():
    """Two `compose up` runs racing for the same container is the worst outcome here."""
    service = _service()

    def _boom(all=False, filters=None):
        raise DockerException("daemon said no")

    service.docker_service.client.containers.list = _boom

    info = await service.preflight()

    assert info["can_upgrade"] is False
    assert "Refusing rather than risking two recreates" in info["blocked_reason"]


@pytest.mark.asyncio
async def test_a_second_start_is_refused_while_the_first_is_in_flight(run_threads_inline):
    service = _service()

    await service.start()
    with pytest.raises(SelfUpgradeRefused) as excinfo:
        await service.start()

    assert "already running" in str(excinfo.value)
    # Still exactly one helper: the second call started nothing.
    assert len(service.docker_service.client.containers.runs) == 1


@pytest.mark.asyncio
async def test_refuses_while_a_run_is_in_flight():
    service = _service()
    service._run = {"run_id": "abc", "phase": "recreating"}

    info = await service.preflight()

    assert info["can_upgrade"] is False
    assert "already running" in info["blocked_reason"]


# ── Preflight allows ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allows_an_unpinned_compose_install_with_a_newer_image():
    info = await _service(active=3).preflight()

    assert info["can_upgrade"] is True
    assert info["blocked_reason"] is None
    assert info["image_ref"] == IMAGE_REF
    assert info["current_digest"].endswith(CURRENT_SHA)
    assert info["available_digest"] == NEWER_SHA
    assert info["up_to_date"] is False
    assert info["pinned"] is False
    assert info["running_executors"] == 3
    assert info["running_bots"] == 2
    assert info["compose"] == {
        "project": "hummingbot-api",
        "working_dir": WORKING_DIR,
        "config_files": [f"{WORKING_DIR}/docker-compose.yml", f"{WORKING_DIR}/docker-compose.tailscale.yml"],
    }


@pytest.mark.asyncio
async def test_a_tailscale_overlay_is_not_a_pin():
    """Only a file named *override* can repoint `image:`; an overlay is networking."""
    info = await _service().preflight()

    assert info["pinned"] is False
    assert info["override_file"] is None
    assert info["can_upgrade"] is True


@pytest.mark.asyncio
async def test_counts_are_reported_even_on_a_blocked_preflight():
    """"You cannot upgrade here" and "17 executors are running" are both the answer."""
    files = f"{WORKING_DIR}/docker-compose.yml,{WORKING_DIR}/docker-compose.override.yml"

    info = await _service(_Client(_container(config_files=files)), active=17).preflight()

    assert info["can_upgrade"] is False
    assert info["running_executors"] == 17
    assert info["running_bots"] == 2


# ── Start refuses ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_refuses_a_blocked_preflight_without_touching_anything():
    service = _service(_Client(_container(), registry_sha=CURRENT_SHA))

    with pytest.raises(SelfUpgradeRefused) as excinfo:
        await service.start(acknowledge_executor_loss=True)

    assert "Already running" in str(excinfo.value)
    assert service.docker_service.pulled == []
    assert service.docker_service.client.containers.runs == []
    assert service.status()["phase"] == "idle"


@pytest.mark.asyncio
async def test_start_refuses_running_executors_without_acknowledgement():
    service = _service(active=4)

    with pytest.raises(SelfUpgradeRefused) as excinfo:
        await service.start()

    assert "4 executor(s) are running" in str(excinfo.value)
    assert "SYSTEM_CLEANUP" in str(excinfo.value)
    assert service.docker_service.pulled == []
    assert service.docker_service.client.containers.runs == []


@pytest.mark.asyncio
async def test_start_refuses_a_pinned_server_even_when_the_loss_is_acknowledged():
    """Acknowledging the executor loss does not buy past the pin."""
    files = f"{WORKING_DIR}/docker-compose.yml,{WORKING_DIR}/docker-compose.override.yml"
    service = _service(_Client(_container(config_files=files)), active=1)

    with pytest.raises(SelfUpgradeRefused) as excinfo:
        await service.start(acknowledge_executor_loss=True)

    assert "docker-compose.override.yml" in str(excinfo.value)
    assert service.docker_service.client.containers.runs == []


# ── Start proceeds ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_pulls_both_images_then_runs_the_helper(run_threads_inline):
    service = _service()

    started = await service.start()

    assert started["phase"] == "pulling"
    assert started["run_id"]
    # Both images on the box before anything is stopped.
    assert service.docker_service.pulled == [IMAGE_REF, HELPER_IMAGE]

    run = service.docker_service.client.containers.runs[0]
    assert run["image"] == HELPER_IMAGE
    assert run["detach"] is True
    assert run["command"] == [
        "docker", "compose", "-p", "hummingbot-api",
        "-f", f"{WORKING_DIR}/docker-compose.yml",
        "-f", f"{WORKING_DIR}/docker-compose.tailscale.yml",
        "up", "-d", "--no-deps", SERVICE_NAME,
    ]
    # Same host path on both sides, or ./bots, .env and the override file resolve to
    # nothing inside the helper.
    assert run["volumes"][WORKING_DIR]["bind"] == WORKING_DIR
    assert run["volumes"]["/var/run/docker.sock"]["bind"] == "/var/run/docker.sock"
    assert run["working_dir"] == WORKING_DIR
    assert run["labels"][HELPER_LABEL] == started["run_id"]
    assert run["labels"][HELPER_PREVIOUS_DIGEST_LABEL].endswith(CURRENT_SHA)
    # The record has to outlive the helper; auto_remove would delete it.
    assert run["auto_remove"] is False

    status = service.status()
    assert status["phase"] == "recreating"
    assert status["previous_digest"].endswith(CURRENT_SHA)


@pytest.mark.asyncio
async def test_start_proceeds_when_the_executor_loss_is_acknowledged(run_threads_inline):
    service = _service(active=2)

    await service.start(acknowledge_executor_loss=True)

    assert len(service.docker_service.client.containers.runs) == 1


@pytest.mark.asyncio
async def test_no_remove_orphans_and_only_the_api_service_is_recreated(run_threads_inline):
    """postgres, emqx, any sidecar and every bot container must be left alone."""
    service = _service()

    await service.start()

    command = service.docker_service.client.containers.runs[0]["command"]
    assert "--remove-orphans" not in command
    assert "--no-deps" in command
    assert command[-1] == SERVICE_NAME


# ── A pull that fails changes nothing ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_api_image_pull_never_starts_the_helper(run_threads_inline):
    service = _service(pull_fails=[IMAGE_REF])

    await service.start()

    assert service.docker_service.client.containers.runs == []
    status = service.status()
    assert status["phase"] == "failed"
    assert "Nothing was changed" in status["detail"]
    # The daemon's own string names the socket and API version; it stays in the log.
    assert "http+docker" not in status["detail"]


@pytest.mark.asyncio
async def test_a_failed_helper_image_pull_never_starts_the_helper(run_threads_inline):
    """The helper image is pulled up front precisely so this failure is harmless."""
    service = _service(pull_fails=[HELPER_IMAGE])

    await service.start()

    assert service.docker_service.client.containers.runs == []
    assert service.status()["phase"] == "failed"


@pytest.mark.asyncio
async def test_a_helper_that_cannot_be_started_is_reported_not_raised(run_threads_inline):
    service = _service()

    def _boom(image, **kwargs):
        raise DockerException("no such image")

    service.docker_service.client.containers.run = _boom

    await service.start()

    status = service.status()
    assert status["phase"] == "failed"
    assert "Nothing was changed" in status["detail"]


# ── Collecting a helper that did not replace this API ────────────────────────────


@pytest.mark.asyncio
async def test_preflight_collects_a_failed_helper_instead_of_blocking_on_it():
    # The helper exited non-zero and this API was never replaced, so no boot will ever
    # collect it. Blocking on it would need SSH to clear -- what this endpoint replaces.
    helper = _helper(exit_code=1, logs=b"service bogus-service depends on undefined service\n")
    service = _service(_Client(_container(), helpers=[helper]))

    info = await service.preflight()

    assert info["can_upgrade"] is True
    assert helper.removed == [True]
    status = service.status()
    assert status["phase"] == "failed"
    assert status["exit_code"] == 1
    assert status["log_tail"] == ["service bogus-service depends on undefined service"]


@pytest.mark.asyncio
async def test_status_does_not_stay_recreating_after_the_helper_fails(run_threads_inline):
    client = _Client(_container())
    service = _service(client)
    run_id = (await service.start())["run_id"]
    assert service.status()["phase"] == "recreating"

    client.containers.helpers.append(_helper(exit_code=1, run_id=run_id))

    status = service.status()
    assert status["phase"] == "failed"
    assert status["run_id"] == run_id
    assert status["exit_code"] == 1
    assert "did not replace this API" in status["detail"]


@pytest.mark.asyncio
async def test_a_helper_that_exits_zero_without_replacing_this_api_is_not_done():
    # We are still here, so the recreate did not happen whatever compose's exit code.
    helper = _helper(exit_code=0)
    service = _service(_Client(_container(), helpers=[helper]))

    await service.preflight()

    assert service.status()["phase"] == "failed"
    assert helper.removed == [True]


@pytest.mark.asyncio
async def test_status_leaves_a_helper_that_is_still_running(run_threads_inline):
    client = _Client(_container())
    service = _service(client)
    run_id = (await service.start())["run_id"]
    helper = _helper(status="running", run_id=run_id)
    client.containers.helpers.append(helper)

    assert service.status()["phase"] == "recreating"
    assert helper.removed == []


# ── Collecting the run on boot ───────────────────────────────────────────────────


def test_collect_on_boot_reports_a_successful_recreate_and_removes_the_helper():
    helper = _helper(exit_code=0)
    client = _Client(_container(digests=(f"hummingbot/hummingbot-api@{NEWER_SHA}",)), helpers=[helper])
    service = _service(client)

    service.collect_on_boot()

    status = service.status()
    assert status["phase"] == "done"
    assert status["run_id"] == "run123"
    assert status["exit_code"] == 0
    assert status["previous_digest"].endswith(CURRENT_SHA)
    assert status["new_digest"].endswith(NEWER_SHA)
    assert helper.removed == [True]


def test_collect_on_boot_reports_a_failed_recreate_with_the_log_tail():
    helper = _helper(exit_code=1, logs=b"service hummingbot-api: error\nvolume not found\n")
    service = _service(_Client(_container(), helpers=[helper]))

    service.collect_on_boot()

    status = service.status()
    assert status["phase"] == "failed"
    assert status["exit_code"] == 1
    assert status["log_tail"] == ["service hummingbot-api: error", "volume not found"]
    assert "docker compose logs hummingbot-api" in status["detail"]
    # Still removed: a failed run must not block the next preflight forever.
    assert helper.removed == [True]


def test_collect_on_boot_leaves_a_helper_that_is_still_running():
    helper = _helper(status="running")
    service = _service(_Client(_container(), helpers=[helper]))

    service.collect_on_boot()

    assert helper.removed == []
    assert service.status()["phase"] == "idle"


def test_collect_on_boot_survives_an_unreachable_daemon():
    service = _service(_Client(_container(), reachable=False))

    service.collect_on_boot()

    assert service.status()["phase"] == "idle"


def test_status_is_idle_before_anything_has_run():
    assert _service().status() == {"run_id": None, "phase": "idle", "detail": None, "log_tail": []}


# ── The routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def make_client():
    """A TestClient whose /system/upgrade/* talks to the given SelfUpgradeService."""
    from deps import get_self_upgrade_service
    from routers import system

    def _make(service):
        app = FastAPI()
        app.include_router(system.router)
        app.dependency_overrides[get_self_upgrade_service] = lambda: service
        return TestClient(app)

    return _make


def test_preflight_route_reports_the_refusal_rather_than_failing(make_client):
    body = make_client(_service(_Client(_container(), reachable=False))).get("/system/upgrade/preflight")

    assert body.status_code == 200
    assert body.json()["can_upgrade"] is False
    assert body.json()["blocked_reason"]


def test_upgrade_route_answers_409_when_refused(make_client):
    response = make_client(_service(active=2)).post("/system/upgrade", json={})

    assert response.status_code == 409
    assert "2 executor(s) are running" in response.json()["detail"]


def test_upgrade_route_with_no_body_acknowledges_nothing(make_client):
    """An empty body must be read as consent withheld, not consent given."""
    response = make_client(_service(active=2)).post("/system/upgrade")

    assert response.status_code == 409


def test_upgrade_route_answers_202_when_it_starts(make_client, run_threads_inline):
    service = _service(active=2)

    response = make_client(service).post("/system/upgrade", json={"acknowledge_executor_loss": True})

    assert response.status_code == 202
    assert response.json()["phase"] == "pulling"
    assert response.json()["run_id"]


def test_status_route_serves_the_collected_run(make_client):
    service = _service(_Client(_container(), helpers=[_helper(exit_code=0)]))
    service.collect_on_boot()

    body = make_client(service).get("/system/upgrade/status").json()

    assert body["phase"] == "done"
    assert body["run_id"] == "run123"
