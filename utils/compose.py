"""How this API's own container was deployed, read from Docker Compose's own labels.

Compose stamps every container it creates with the project name, the directory it was
run from and the config files it was run with. Those labels are the only record on the
box of how the deployment is put together -- there is nothing to configure and nothing
to guess -- so both things that need to know read them from here:

* ``GET /system/info`` (FEAT-121) reports them, and reports whether the running image is
  the published one or something pinned on the box.
* ``services/self_upgrade.py`` (FEAT-122) *refuses to upgrade* when it is pinned, and
  rebuilds the exact ``docker compose -f ... up`` the operator would have typed.

They share this module rather than each reading the labels themselves because the two
answers have to agree. A panel that says "not pinned" over an upgrade path that refuses
as pinned is a confusing afternoon; a panel that says "not pinned" over an upgrade path
that *proceeds* on a hand-built image is a trading API server replaced by a published
build nobody asked for.
"""

import os
import re
import socket

# The image this API is published as. An image from any other repository -- a local
# `docker build -t hbapi .`, a fork -- is by definition not the published one.
PUBLISHED_REPO = "hummingbot/hummingbot-api"
PUBLISHED_TAG = "latest"

# Compose service this API is deployed as, used to find our own container when the
# hostname lookup fails (see own_container).
COMPOSE_SERVICE = "hummingbot-api"

# Compose stamps these on every container it creates; they say which project the API
# belongs to and which files were used to bring it up.
LABEL_PROJECT = "com.docker.compose.project"
LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"


def _container_id_from_mountinfo():
    """This process's own container id, read from the files Docker bind-mounts into it.

    Docker mounts /var/lib/docker/containers/<id>/{hostname,hosts,resolv.conf} into every
    container, so the id is in our own mount table whatever `hostname:` was set to.
    """
    try:
        with open("/proc/self/mountinfo") as f:
            match = re.search(r"/containers/([0-9a-f]{64})/", f.read())
    except OSError:
        return None
    return match.group(1) if match else None


def _in_container():
    return os.path.exists("/.dockerenv") or _container_id_from_mountinfo() is not None


def own_container(client):
    """Find the container this API process runs in.

    Docker sets a container's hostname to its own short id, which is how a process
    identifies itself from the inside; when the deployment overrides `hostname:` the id
    is still in the mount table. Only when both miss, and only from inside a container,
    fall back to the compose service label -- and only when exactly one container carries
    it. A process running from source on the host is not any container, and with two
    candidates picking one would report another deployment's image and pinning as ours:
    None is the honest answer in both cases.

    Returns:
        A docker-py Container, or None when this process cannot be placed.
    """
    for ref in (socket.gethostname(), _container_id_from_mountinfo()):
        if not ref:
            continue
        try:
            return client.containers.get(ref)
        except Exception:
            pass
    if not _in_container():
        return None
    try:
        matches = client.containers.list(filters={"label": f"com.docker.compose.service={COMPOSE_SERVICE}"})
    except Exception:
        return None
    return matches[0] if len(matches) == 1 else None


def _repository(ref):
    """The repository of an image reference, without tag, digest or the Docker Hub host.

    ``docker.io/hummingbot/hummingbot-api:latest`` and ``hummingbot/hummingbot-api@sha256:..``
    are both ``hummingbot/hummingbot-api``. A colon after the last slash is a tag; one
    before it is a registry port.
    """
    repo = (ref or "").split("@")[0]
    if ":" in repo.rsplit("/", 1)[-1]:
        repo = repo.rsplit(":", 1)[0]
    for host in ("docker.io/", "index.docker.io/"):
        if repo.startswith(host):
            repo = repo[len(host):]
    return repo


def _tag(ref):
    """The tag of an image reference; an untagged reference means ``latest``."""
    name = (ref or "").split("@")[0].rsplit("/", 1)[-1]
    return name.rsplit(":", 1)[1] if ":" in name else "latest"


def describe_container(container):
    """Identity, image and compose provenance of a container.

    Returns:
        Dictionary with the container's id and name, the image reference it was started
        from, the image id, its digest in the published repository (None when it has
        none, e.g. a locally built image) and the three compose labels that say where the
        deployment lives.
    """
    labels = container.labels or {}
    image = container.image
    # Only a digest in the published repository says the image came from it. With the
    # legacy image store a local build has no RepoDigests at all, but with the containerd
    # store (the default on current Docker Desktop) `docker build -t hbapi .` gets one too
    # -- `hbapi@sha256:...` -- and taking that as a registry digest reads a hand-built
    # image as the published one.
    digests = [
        d for d in ((image.attrs.get("RepoDigests") or []) if image is not None else [])
        if _repository(d) == PUBLISHED_REPO
    ]
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


def config_files(described):
    """The compose config files of a described container, as a list of host paths.

    The label holds them comma-separated in the order they were passed to compose, and
    that order is load-bearing: later files override earlier ones, so replaying it as
    `-f a -f b` is the only way to recreate the container the operator actually has.
    """
    raw = (described or {}).get("compose_config_files") or ""
    return [f.strip() for f in raw.split(",") if f.strip()]


def pinning(described):
    """Whether this deployment runs something other than the published image.

    Three things make an image not the one `hummingbot/hummingbot-api:latest` currently
    resolves to: it was built on the box or comes from another repository (no digest in
    the published one), it runs a tag other than `latest` (a published version pinned in
    docker-compose.yml itself), or compose was brought up with an override file that can
    repoint `image:` at a pinned tag. Only files whose name contains "override" count --
    `docker-compose.tailscale.yml` is an overlay for networking, not for the image.

    The one case this cannot see is an image built locally *as*
    `hummingbot/hummingbot-api:latest` on the containerd store, which carries a digest in
    the published repository like a pulled image does; nothing local tells them apart.

    Returns:
        Dictionary with pinned, a short pinned_reason and the override file name (None
        when that is not the reason).
    """
    files = config_files(described)
    override_file = next((os.path.basename(f) for f in files if "override" in os.path.basename(f).lower()), None)

    image_ref = (described or {}).get("image")
    reasons = []
    if not (described or {}).get("digest"):
        reasons.append(f"image was built locally or is not from {PUBLISHED_REPO} (no published digest)")
    elif "@" in (image_ref or ""):
        reasons.append("image is pinned to a digest")
    elif _tag(image_ref) != PUBLISHED_TAG:
        reasons.append(f"image is pinned to tag {_tag(image_ref)}")
    if override_file:
        reasons.append(f"compose override file {override_file} can pin the image")

    return {
        "pinned": bool(reasons),
        "pinned_reason": "; ".join(reasons) or None,
        "override_file": override_file,
    }
