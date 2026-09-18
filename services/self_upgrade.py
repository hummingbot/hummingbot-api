"""Upgrade this API's own container to the published image (FEAT-122).

Today upgrading a hummingbot-api server means ssh-ing to the host and running
``docker compose pull && docker compose up -d``. Condor's own updater only knows how to
do that in a *local* checkout, so every remote server is a manual job and servers drift.

**Why a helper container.** A process cannot recreate its own container: ``compose up``
stops the old container, which kills the process running compose halfway through. So the
API pulls the image in-process -- the thing it already does for every other image -- and
then hands the recreate to a one-shot ``docker:cli`` container that survives the API's
restart. That helper is also the run record: its exit code and logs say what happened,
and the *new* API collects them on boot. There is no state file and no DB table.

**Everything here defaults to refusing.** The failure mode of this module is a trading
API server that does not come back, so ``preflight`` starts from "blocked" and only
clears that at the very end, once every fact it needs has actually been established.
Not established is the same as blocked: an unreachable Docker daemon, a container it
cannot place, missing compose labels, a registry it cannot read, an executor count it
cannot take. None of those are "probably fine".

**What it will not do.** It never upgrades a *pinned* deployment -- an image built on the
box, or a compose override file that can repoint ``image:``. Removing an override or
swapping a hand-built tag is an operator's decision about their own host, and silently
rolling such a server onto the published build is the one outcome worse than not
upgrading at all. Those servers stay an SSH job and the reason names the override file.

Out of scope, deliberately: switching tags or images, editing override files, rebuilding
from source, upgrading postgres/emqx/gateway, and rollback.
"""

import logging
import threading
import time
import uuid
from typing import Any, Dict, Optional

from utils.compose import config_files, describe_container, own_container, pinning

logger = logging.getLogger(__name__)

# Ships the compose plugin. Pinned to a major tag rather than :latest so the helper that
# recreates this API is not itself a moving target.
HELPER_IMAGE = "docker:27-cli"

# Carries the run id, and is how a helper -- running or exited -- is found again after
# the API that started it has been replaced.
HELPER_LABEL = "hummingbot-api.self-upgrade"
HELPER_PREVIOUS_DIGEST_LABEL = "hummingbot-api.self-upgrade.previous-digest"

# The compose service to recreate. Only this one: postgres, emqx, any tailscale sidecar
# and every bot container are left exactly as they are.
SERVICE_NAME = "hummingbot-api"

# Lines of the helper's output kept as the run record.
LOG_TAIL_LINES = 40


class SelfUpgradeRefused(Exception):
    """This upgrade will not be started, and nothing has been changed.

    Raised before any pull and before any container is touched, so a caller that sees it
    knows the server is exactly as it was. The router answers 409.
    """


def _sha(digest: Optional[str]) -> Optional[str]:
    """The ``sha256:...`` part of a digest, however it was spelled.

    A container's ``RepoDigests`` entry is ``repo@sha256:...`` while the registry answers
    with the bare ``sha256:...``; comparing the two as written would make every server
    look out of date forever.
    """
    if not digest:
        return None
    return digest.split("@")[-1].strip() or None


class SelfUpgradeService:
    """Preflight, start and report on replacing this API's container.

    Args:
        docker_service: The API's ``DockerService``; its ``client`` is the daemon handle
            and its ``pull_image_sync`` is the pull already used everywhere else.
        executor_service: Source of the running-executor count, the number that decides
            whether this upgrade destroys anything.
        bots_orchestrator: Source of the running-bot count, which is reported so the
            operator can see that bots are *not* affected.
    """

    def __init__(self, docker_service, executor_service=None, bots_orchestrator=None):
        self.docker_service = docker_service
        self.executor_service = executor_service
        self.bots_orchestrator = bots_orchestrator
        self._lock = threading.Lock()
        self._run: Optional[Dict[str, Any]] = None

    # ── Facts ────────────────────────────────────────────────────────────────────

    @property
    def client(self):
        """The Docker daemon handle, or None when it was never established."""
        return getattr(self.docker_service, "client", None)

    def _daemon(self):
        """The daemon handle if it answers a ping, else None. Never raises."""
        client = self.client
        if client is None:
            return None
        try:
            client.ping()
        except Exception as e:
            logger.warning(f"Docker daemon unreachable while preparing a self-upgrade: {e}")
            return None
        return client

    def _registry_digest(self, image_ref: str) -> Optional[str]:
        """The digest the registry currently serves for ``image_ref``, or None.

        None is a *blocking* answer, not an optimistic one: without it there is no
        evidence that a newer image exists, and pulling a tag on that basis is how a
        server gets recreated for nothing.
        """
        client = self.client
        try:
            return _sha(client.images.get_registry_data(image_ref).id)
        except Exception as e:
            logger.warning(f"Could not read the registry digest for {image_ref}: {e}")
            return None

    def _existing_helper(self, client):
        """``(established, helper)`` for a helper container already on this box.

        ``established`` is False when the daemon would not answer the question at all,
        which blocks: an upgrade started while another one is mid-recreate is two
        ``compose up`` runs racing for the same container, and "I could not check" is not
        a reason to start the second one.
        """
        try:
            found = client.containers.list(all=True, filters={"label": HELPER_LABEL})
        except Exception as e:
            logger.warning(f"Could not list self-upgrade helper containers: {e}")
            return False, None
        return True, (found[0] if found else None)

    async def _running_executors(self) -> Optional[int]:
        """How many executors are live, or None when that could not be established."""
        if self.executor_service is None:
            return None
        try:
            return int(self.executor_service.get_summary().get("total_active", 0))
        except Exception as e:
            logger.warning(f"Could not count running executors before a self-upgrade: {e}")
            return None

    async def _running_bots(self) -> Optional[int]:
        """How many bot containers are running, or None when that is unknown."""
        if self.bots_orchestrator is None:
            return None
        try:
            return len(await self.bots_orchestrator.get_active_containers())
        except Exception as e:
            logger.warning(f"Could not count running bots before a self-upgrade: {e}")
            return None

    # ── Preflight ────────────────────────────────────────────────────────────────

    async def preflight(self) -> Dict[str, Any]:
        """Everything the caller needs to decide, and whether it may proceed at all.

        Returns:
            Dictionary with the image reference and its current/available digests,
            ``up_to_date``, the pinning verdict, the compose coordinates, the counts of
            running executors and bots, and ``can_upgrade`` with a ``blocked_reason``
            that is None only when the upgrade may actually be started.

        Never raises: this is what a dashboard polls, and a daemon hiccup has to render
        as a refusal with a reason, not as a 500.
        """
        info: Dict[str, Any] = {
            "service": SERVICE_NAME,
            "helper_image": HELPER_IMAGE,
            "image_ref": None,
            "current_digest": None,
            "available_digest": None,
            "up_to_date": None,
            "pinned": None,
            "pinned_reason": None,
            "override_file": None,
            "compose": None,
            "running_executors": None,
            "running_bots": None,
            "can_upgrade": False,
            # Refusing is the default. Every branch below either replaces this reason
            # with a more specific one or falls through to the single place that clears
            # it, so a path that forgets to decide still refuses.
            "blocked_reason": "This server has not established that it can upgrade itself.",
        }

        client = self._daemon()
        if client is None:
            info["blocked_reason"] = (
                "The Docker daemon is not reachable from this API, so it cannot upgrade itself. "
                "Check that /var/run/docker.sock is mounted into the container."
            )
            return info

        container = own_container(client)
        if container is None:
            info["blocked_reason"] = (
                "This API could not identify its own container, so it does not know what to recreate. "
                "Upgrade this server over SSH."
            )
            return info

        try:
            described = describe_container(container)
        except Exception as e:
            logger.warning(f"Could not inspect the API's own container before a self-upgrade: {e}")
            info["blocked_reason"] = (
                "This API could not inspect its own container, so it does not know what to recreate. "
                "Upgrade this server over SSH."
            )
            return info

        info["image_ref"] = described.get("image")
        info["current_digest"] = described.get("digest")
        info.update(pinning(described))

        project = described.get("compose_project")
        working_dir = described.get("compose_working_dir")
        files = config_files(described)
        if project and working_dir and files:
            info["compose"] = {"project": project, "working_dir": working_dir, "config_files": files}

        # Counts first: they are reported even on a blocked preflight, because "you
        # cannot upgrade here" and "17 executors are running" are both things the
        # operator came to find out.
        info["running_executors"] = await self._running_executors()
        info["running_bots"] = await self._running_bots()

        if info["compose"] is None:
            info["blocked_reason"] = (
                "This API was not started by Docker Compose (no compose labels on its container), "
                "so there is no compose project to recreate it from. Upgrade this server over SSH."
            )
            return info

        if info["pinned"]:
            info["blocked_reason"] = (
                f"This server's image is pinned: {info['pinned_reason']}. "
                "Pulling the published image would undo that, so it is refused. "
                "Upgrade this server over SSH."
            )
            return info

        if not info["image_ref"]:
            info["blocked_reason"] = (
                "This API's container does not report the image it was started from, "
                "so there is nothing to compare against the registry."
            )
            return info

        available = self._registry_digest(info["image_ref"])
        info["available_digest"] = available
        if available is None:
            info["blocked_reason"] = (
                f"Could not read the published digest for {info['image_ref']} from the registry, "
                "so there is no evidence that a newer image exists. Nothing was pulled."
            )
            return info

        info["up_to_date"] = _sha(info["current_digest"]) == available
        if info["up_to_date"]:
            info["blocked_reason"] = f"Already running the published {info['image_ref']}."
            return info

        if info["running_executors"] is None:
            info["blocked_reason"] = (
                "Could not determine how many executors are running, and a restart closes every "
                "running executor as SYSTEM_CLEANUP. Refusing rather than guessing."
            )
            return info

        established, helper = self._existing_helper(client)
        if not established:
            info["blocked_reason"] = (
                "Could not check whether an upgrade is already running on this server. "
                "Refusing rather than risking two recreates of the same container."
            )
            return info
        if helper is not None:
            info["blocked_reason"] = (
                f"An upgrade helper container is already present ({helper.name}). "
                "Wait for it to finish; it is cleared when this API next starts."
            )
            return info

        with self._lock:
            phase = (self._run or {}).get("phase")
        if phase in ("pulling", "recreating"):
            info["blocked_reason"] = "An upgrade is already running on this server."
            return info

        # The one place the refusal is lifted.
        info["can_upgrade"] = True
        info["blocked_reason"] = None
        return info

    # ── Start ────────────────────────────────────────────────────────────────────

    async def start(self, acknowledge_executor_loss: bool = False) -> Dict[str, Any]:
        """Pull the published image and hand the recreate to a helper container.

        The preflight is re-run here rather than trusted from the caller's earlier read:
        between the dialog opening and the button being pressed, an executor can start,
        another operator can begin an upgrade, or the daemon can go away.

        Args:
            acknowledge_executor_loss: The caller has been told that every running
                executor will be closed as ``SYSTEM_CLEANUP`` and is not coming back.

        Returns:
            ``{"run_id": ..., "phase": "pulling"}``.

        Raises:
            SelfUpgradeRefused: Nothing was pulled and nothing was touched.
        """
        pre = await self.preflight()
        if pre["blocked_reason"]:
            raise SelfUpgradeRefused(pre["blocked_reason"])

        running = pre["running_executors"] or 0
        if running > 0 and not acknowledge_executor_loss:
            raise SelfUpgradeRefused(
                f"{running} executor(s) are running. Restarting this API closes every one of them as "
                "SYSTEM_CLEANUP and they are not restored. Confirm that loss to proceed."
            )

        run_id = uuid.uuid4().hex[:12]
        compose = pre["compose"]
        with self._lock:
            # Re-checked under the lock: two callers can both clear the preflight before
            # either has claimed the run, and the loser would start a second `compose up`
            # against the container the first is already replacing.
            in_flight = (self._run or {}).get("phase")
            if in_flight in ("pulling", "recreating"):
                raise SelfUpgradeRefused("An upgrade is already running on this server.")
            self._run = {
                "run_id": run_id,
                "phase": "pulling",
                "detail": f"Pulling {pre['image_ref']}",
                "image_ref": pre["image_ref"],
                "previous_digest": pre["current_digest"],
                "new_digest": None,
                "exit_code": None,
                "log_tail": [],
                "started_at": time.time(),
            }

        threading.Thread(
            target=self._pull_then_recreate,
            args=(run_id, pre["image_ref"], compose, pre["current_digest"]),
            daemon=True,
            name=f"self-upgrade-{run_id}",
        ).start()

        return {"run_id": run_id, "phase": "pulling"}

    def _set(self, run_id: str, **fields) -> None:
        """Update the current run, ignoring a write for a run that has been replaced."""
        with self._lock:
            if not self._run or self._run.get("run_id") != run_id:
                return
            self._run.update(fields)

    def _pull_then_recreate(self, run_id, image_ref, compose, previous_digest) -> None:
        """The whole upgrade, off the event loop. Never raises out of the thread.

        Order matters and is the safety property: both images are on the box *before*
        anything is stopped. A pull that fails leaves the server untouched and running,
        which is why the helper image is pulled here too rather than being left to
        ``containers.run``'s implicit pull -- that one would happen with the recreate
        already committed.
        """
        for ref in (image_ref, HELPER_IMAGE):
            self._set(run_id, phase="pulling", detail=f"Pulling {ref}")
            result = self.docker_service.pull_image_sync(ref)
            if not (isinstance(result, dict) and result.get("success")):
                # The daemon's own string carries the socket URL and API version, so it
                # goes to the log; the caller gets the image that failed.
                logger.error(f"Self-upgrade {run_id}: pull of {ref} failed: {result}")
                self._set(
                    run_id,
                    phase="failed",
                    detail=f"Could not pull {ref}. Nothing was changed; see the API log for the daemon's error.",
                    finished_at=time.time(),
                )
                return

        command = ["docker", "compose", "-p", compose["project"]]
        for path in compose["config_files"]:
            command += ["-f", path]
        command += ["up", "-d", "--no-deps", SERVICE_NAME]

        working_dir = compose["working_dir"]
        try:
            helper = self.client.containers.run(
                HELPER_IMAGE,
                command=command,
                detach=True,
                labels={
                    HELPER_LABEL: run_id,
                    HELPER_PREVIOUS_DIGEST_LABEL: previous_digest or "",
                },
                volumes={
                    "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                    # Bound at the *same* host path so the relative paths inside the
                    # compose files -- ./bots, .env, the override file -- resolve to the
                    # same things they resolved to when the operator brought the stack up.
                    working_dir: {"bind": working_dir, "mode": "rw"},
                },
                working_dir=working_dir,
                # auto_remove would delete the run record along with the container, and
                # the record is the only thing that survives this API being replaced.
                auto_remove=False,
            )
        except Exception as e:
            logger.error(f"Self-upgrade {run_id}: could not start the helper container: {e}")
            self._set(
                run_id,
                phase="failed",
                detail="Could not start the upgrade helper container. Nothing was changed.",
                finished_at=time.time(),
            )
            return

        logger.info(f"Self-upgrade {run_id}: helper {helper.name} running `{' '.join(command)}`")
        self._set(
            run_id,
            phase="recreating",
            detail=(
                "Recreating the hummingbot-api container. This API is about to be replaced; "
                "the new one reports the result."
            ),
            helper=getattr(helper, "name", None),
        )

    # ── Report ───────────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """The current or last run, or an idle record when there has been none."""
        with self._lock:
            if self._run is None:
                return {"run_id": None, "phase": "idle", "detail": None, "log_tail": []}
            return dict(self._run)

    def collect_on_boot(self) -> None:
        """Adopt the outcome of a helper that outlived the API that started it.

        This is where an upgrade actually finishes: the process that started it is gone,
        and the record of what happened is the exited helper container. Its exit code and
        log tail become this API's last run, then it is removed so the next preflight is
        not blocked by its own predecessor.

        A helper still running is left alone -- it may be mid-recreate, and preflight
        blocks on it. Never raises: a failure to tidy up must not stop the API booting.
        """
        client = self._daemon()
        if client is None:
            return
        try:
            helpers = client.containers.list(all=True, filters={"label": HELPER_LABEL})
        except Exception as e:
            logger.warning(f"Could not look for self-upgrade helper containers on boot: {e}")
            return

        for helper in helpers:
            # A helper that has not exited is very likely running `docker compose up`
            # right now. Removing it would kill the recreate half-way, which is the one
            # thing this whole module is built to avoid, so it is left alone -- and
            # preflight blocks on it until it finishes.
            if getattr(helper, "status", None) not in ("exited", "dead"):
                logger.info(f"Self-upgrade helper {helper.name} is still {helper.status}; leaving it.")
                continue
            try:
                labels = helper.labels or {}
                run_id = labels.get(HELPER_LABEL)
                previous_digest = labels.get(HELPER_PREVIOUS_DIGEST_LABEL) or None
                exit_code = (helper.attrs.get("State") or {}).get("ExitCode")
                try:
                    raw = helper.logs(tail=LOG_TAIL_LINES)
                    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                    log_tail = [line for line in text.splitlines() if line.strip()]
                except Exception as e:
                    logger.warning(f"Could not read logs of self-upgrade helper {helper.name}: {e}")
                    log_tail = []

                succeeded = exit_code == 0
                with self._lock:
                    self._run = {
                        "run_id": run_id,
                        "phase": "done" if succeeded else "failed",
                        "detail": (
                            "Upgrade finished; this API is the new container."
                            if succeeded
                            else f"The recreate failed (exit code {exit_code}). "
                            "The previous container may still be running; check "
                            "`docker compose logs hummingbot-api` on the host."
                        ),
                        "image_ref": None,
                        "previous_digest": previous_digest,
                        "new_digest": self._current_digest(client),
                        "exit_code": exit_code,
                        "log_tail": log_tail,
                        "finished_at": time.time(),
                    }
                logger.info(f"Collected self-upgrade helper {helper.name} (exit {exit_code})")
            except Exception as e:
                logger.warning(f"Could not read self-upgrade helper {getattr(helper, 'name', '?')}: {e}")
            finally:
                try:
                    helper.remove(force=True)
                except Exception as e:
                    logger.warning(f"Could not remove self-upgrade helper {getattr(helper, 'name', '?')}: {e}")

    def _current_digest(self, client) -> Optional[str]:
        """The digest this API is running right now, or None when it cannot be read."""
        try:
            container = own_container(client)
            if container is None:
                return None
            return describe_container(container).get("digest")
        except Exception as e:
            logger.warning(f"Could not read this API's own digest after an upgrade: {e}")
            return None
