import os

from fastapi import APIRouter, Depends, HTTPException

from deps import get_bot_archiver, get_docker_service
from models import DockerImage
from services.docker_service import DockerService
from utils.bot_archiver import BotArchiver

router = APIRouter(tags=["Docker"], prefix="/docker")

# What a DockerService failure means over HTTP. The service reports the kind of failure
# rather than raising, because its internal callers (stop-and-archive's retry loop) are
# built to carry on past one; translating it is the HTTP layer's job.
_STATUS_BY_ERROR = {
    "not_found": 404,
    "docker_error": 502,
}


def _require_api_managed_container(container_name: str) -> str:
    """Refuse a container this API did not create, by the only marker that actually
    exists: its instance directory. Container names carry no prefix, so this is the
    one check shared by remove/stop/start -- a traversing name is refused here too,
    before any of them reach the daemon.
    """
    try:
        instance_dir = DockerService.resolve_instance_dir(container_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not os.path.isdir(instance_dir):
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint only removes bot containers managed by this API. Container "
                   f"'{container_name}' has no instance directory at '{instance_dir}'."
        )
    return instance_dir


def _or_http_error(result):
    """Turn a DockerService failure into a status code instead of a 200 with an error body.

    These routes used to return the service's error verbatim, so a container that does
    not exist answered 200 with a raw docker-py string -- a caller checking the status
    code, which is how a caller checks, read it as a container that had been stopped.

    Only a dict carrying `success: False` is a failure; a listing route's list and a
    payload like {"images": [...]} pass through untouched.
    """
    if isinstance(result, dict) and result.get("success") is False:
        raise HTTPException(
            status_code=_STATUS_BY_ERROR.get(result.get("error"), 502),
            detail=result.get("message", "Docker operation failed"),
        )
    return result


@router.get("/running")
async def is_docker_running(docker_service: DockerService = Depends(get_docker_service)):
    """
    Check if Docker daemon is running.

    Args:
        docker_service: Docker service dependency

    Returns:
        Dictionary indicating if Docker is running
    """
    return docker_service.is_docker_running()


@router.get("/available-images/")
async def available_images(image_name: str = None, docker_service: DockerService = Depends(get_docker_service)):
    """
    Get available Docker images matching the specified name.

    Args:
        image_name: Name pattern to search for in image tags
        docker_service: Docker service dependency

    Returns:
        Dictionary with list of available image tags

    Raises:
        HTTPException: 502 if the Docker daemon refused or could not be reached
    """
    available_images = _or_http_error(docker_service.get_available_images())
    if image_name:
        return [tag for image in available_images["images"] for tag in image.tags if image_name in tag]
    return [tag for tag in available_images["images"]]


@router.get("/active-containers")
async def active_containers(name_filter: str = None, docker_service: DockerService = Depends(get_docker_service)):
    """
    Get all currently active (running) Docker containers.

    Args:
        name_filter: Optional filter to match container names (case-insensitive)
        docker_service: Docker service dependency

    Returns:
        List of active container information

    Raises:
        HTTPException: 502 if the Docker daemon refused or could not be reached
    """
    return _or_http_error(docker_service.get_active_containers(name_filter))


@router.get("/exited-containers")
async def exited_containers(name_filter: str = None, docker_service: DockerService = Depends(get_docker_service)):
    """
    Get all exited (stopped) Docker containers.

    Args:
        name_filter: Optional filter to match container names (case-insensitive)
        docker_service: Docker service dependency

    Returns:
        List of exited container information

    Raises:
        HTTPException: 502 if the Docker daemon refused or could not be reached
    """
    return _or_http_error(docker_service.get_exited_containers(name_filter))


@router.post("/clean-exited-containers")
async def clean_exited_containers(docker_service: DockerService = Depends(get_docker_service)):
    """
    Remove this API's own exited bot containers to free up space.

    NOTE: Only removes containers that own a bots/instances/<name> directory. An
    unrelated exited container on the shared Docker daemon is left in place.

    Args:
        docker_service: Docker service dependency

    Returns:
        {"success": true, "message": ...} once this API's exited bot containers are removed

    Raises:
        HTTPException: 502 if the Docker daemon refused or could not be reached
    """
    return _or_http_error(docker_service.clean_exited_containers())


@router.post("/remove-container/{container_name}")
async def remove_container(
    container_name: str,
    archive_locally: bool = True,
    s3_bucket: str = None,
    docker_service: DockerService = Depends(get_docker_service),
    bot_archiver: BotArchiver = Depends(get_bot_archiver),
):
    """
    Remove a bot container created by this API and archive its bot data.

    NOTE: This endpoint only works with containers this API manages. A bot container is named
    after its instance verbatim, and owns the bots/instances/<container_name> directory that this
    endpoint archives; an unrelated container on the host has no such directory and is refused.

    Args:
        container_name: Name of the bot container to remove
        archive_locally: Whether to archive data locally (default: True)
        s3_bucket: S3 bucket name for cloud archiving (optional)
        docker_service: Docker service dependency
        bot_archiver: Bot archiver service dependency

    Returns:
        Response from container removal operation

    Raises:
        HTTPException: 400 if the container is not a bot managed by this API
        HTTPException: 500 if archiving fails
    """
    instance_dir = _require_api_managed_container(container_name)

    # Remove the container
    # Deliberately not mapped to a status code the way stop/start are: this route also
    # archives, and a container that is already gone is a bot whose data still needs
    # archiving -- a removal that finds nothing to remove has reached the end state the
    # caller asked for. The response carries `success` for a caller that wants to know
    # which of the two happened.
    response = docker_service.remove_container(container_name)
    try:
        # Archive the data
        if archive_locally:
            bot_archiver.archive_locally(container_name, instance_dir)
        else:
            bot_archiver.archive_and_upload(container_name, instance_dir, bucket_name=s3_bucket)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return response


@router.post("/stop-container/{container_name}")
async def stop_container(container_name: str, docker_service: DockerService = Depends(get_docker_service)):
    """
    Stop a running Docker container created by this API.

    NOTE: This endpoint only works with containers this API manages -- see
    remove_container's note on how ownership is determined.

    Args:
        container_name: Name of the container to stop
        docker_service: Docker service dependency

    Returns:
        {"success": true, "message": ...} once the container is stopped

    Raises:
        HTTPException: 400 if the container is not a bot managed by this API
        HTTPException: 404 if no container by that name exists
        HTTPException: 502 if the Docker daemon refused or could not be reached
    """
    _require_api_managed_container(container_name)
    return _or_http_error(docker_service.stop_container(container_name))


@router.post("/start-container/{container_name}")
async def start_container(container_name: str, docker_service: DockerService = Depends(get_docker_service)):
    """
    Start a stopped Docker container created by this API.

    NOTE: This endpoint only works with containers this API manages -- see
    remove_container's note on how ownership is determined.

    Args:
        container_name: Name of the container to start
        docker_service: Docker service dependency

    Returns:
        {"success": true, "message": ...} once the container is started

    Raises:
        HTTPException: 400 if the container is not a bot managed by this API
        HTTPException: 404 if no container by that name exists
        HTTPException: 502 if the Docker daemon refused or could not be reached
    """
    _require_api_managed_container(container_name)
    return _or_http_error(docker_service.start_container(container_name))


@router.post("/pull-image/")
async def pull_image(image: DockerImage, docker_service: DockerService = Depends(get_docker_service)):
    """
    Initiate Docker image pull as background task.
    Returns immediately with task status for monitoring.

    Args:
        image: DockerImage object containing the image name to pull
        docker_service: Docker service dependency

    Returns:
        Status of the pull operation initiation
    """
    result = docker_service.pull_image_async(image.image_name)
    return result


@router.get("/pull-status/")
async def get_pull_status(docker_service: DockerService = Depends(get_docker_service)):
    """
    Get status of all pull operations.

    Args:
        docker_service: Docker service dependency

    Returns:
        Dictionary with all pull operations and their statuses
    """
    return docker_service.get_all_pull_status()
