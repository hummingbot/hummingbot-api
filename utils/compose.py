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

# Compose service this API is deployed as, used to find our own container when the
# hostname lookup fails (see own_container).
COMPOSE_SERVICE = "hummingbot-api"

# Compose stamps these on every container it creates; they say which project the API
# belongs to and which files were used to bring it up.
LABEL_PROJECT = "com.docker.compose.project"
LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"


def own_container(client):
    """Find the container this API process runs in.

    Docker sets a container's hostname to its own short id, which is how a process
    identifies itself from the inside. That fails when the deployment overrides
    `hostname:` or when the API runs from source on the host, so fall back to the one
    container carrying this project's compose service label.

    Returns:
        A docker-py Container, or None when this process cannot be placed.
    """
    import socket

    try:
        return client.containers.get(socket.gethostname())
    except Exception:
        pass
    try:
        matches = client.containers.list(filters={"label": f"com.docker.compose.service={COMPOSE_SERVICE}"})
    except Exception:
        return None
    return matches[0] if matches else None


def describe_container(container):
    """Identity, image and compose provenance of a container.

    Returns:
        Dictionary with the container's id and name, the image reference it was started
        from, the image id, its registry digest (None for a locally built image) and the
        three compose labels that say where the deployment lives.
    """
    labels = container.labels or {}
    image = container.image
    # A locally built image has no RepoDigests: nothing published it, so there is no
    # content address to compare against the tag it carries.
    digests = (image.attrs.get("RepoDigests") or []) if image is not None else []
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

    Two things make an image not the one `hummingbot/hummingbot-api:latest` currently
    resolves to: it was built on the box (no registry digest), or compose was brought up
    with an override file that can repoint `image:` at a pinned tag. Only files whose
    name contains "override" count -- `docker-compose.tailscale.yml` is an overlay for
    networking, not for the image.

    Returns:
        Dictionary with pinned, a short pinned_reason and the override file name (None
        when that is not the reason).
    """
    files = config_files(described)
    override_file = next((os.path.basename(f) for f in files if "override" in os.path.basename(f).lower()), None)

    reasons = []
    if not (described or {}).get("digest"):
        reasons.append("image was built locally (no registry digest)")
    if override_file:
        reasons.append(f"compose override file {override_file} can pin the image")

    return {
        "pinned": bool(reasons),
        "pinned_reason": "; ".join(reasons) or None,
        "override_file": override_file,
    }
