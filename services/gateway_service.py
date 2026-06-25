import logging
import os
import platform
import shutil
from typing import Any, Dict, Optional

import docker
from docker.errors import DockerException
from docker.types import LogConfig

from config import settings
from models.gateway import GatewayConfig, GatewayStatus
from utils.gateway_certs import ensure_gateway_certs

# Create module-specific logger
logger = logging.getLogger(__name__)


class GatewayService:
    """
    Service for managing the Hummingbot Gateway Docker container.
    Ensures only one Gateway instance can exist at a time.
    """

    GATEWAY_CONTAINER_NAME = "gateway"
    # Shared Gateway dir lives UNDER bots/ so it sits inside the single host<->container bind
    # mount (./bots:/hummingbot-api/bots). Anything written outside that mount by a containerized
    # API lands on the container's ephemeral FS and never reaches the Gateway container.
    GATEWAY_SUBPATH = os.path.join("bots", "gateway-files")
    # Path inside the Gateway container where it reads the mTLS cert set.
    GATEWAY_CERTS_BIND = "/home/gateway/certs"

    def __init__(self):
        self.SOURCE_PATH = os.getcwd()
        # Use BOTS_PATH if set (for Docker), otherwise use SOURCE_PATH (for local)
        self.BOTS_PATH = os.environ.get('BOTS_PATH', self.SOURCE_PATH)
        try:
            self.client = docker.from_env()
        except DockerException as e:
            logger.error(f"Failed to connect to Docker. Error: {e}")
            raise

    def _gateway_base(self, host: bool = False) -> str:
        """Gateway-files base. host=False is the path the API process reads/writes
        (container-local); host=True is the host path used as a Docker bind-mount source."""
        base = self.BOTS_PATH if host else self.SOURCE_PATH
        return os.path.join(base, self.GATEWAY_SUBPATH)

    def _ensure_gateway_directories(self):
        """Create necessary directories for Gateway if they don't exist.

        Directories are created on the path the API process can actually write (container-local,
        inside the mounted bots/ dir); the matching host paths are used for the bind mounts.
        """
        gateway_base = self._gateway_base(host=False)

        conf_dir = os.path.join(gateway_base, "conf")
        logs_dir = os.path.join(gateway_base, "logs")
        certs_dir = os.path.join(gateway_base, "certs")

        os.makedirs(conf_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)
        os.makedirs(certs_dir, exist_ok=True)

        return {
            "base": gateway_base,
            "conf": conf_dir,
            "logs": logs_dir,
            "certs": certs_dir,
        }

    def _get_gateway_container(self) -> Optional[docker.models.containers.Container]:
        """Get the Gateway container if it exists"""
        try:
            return self.client.containers.get(self.GATEWAY_CONTAINER_NAME)
        except docker.errors.NotFound:
            return None
        except DockerException as e:
            logger.error(f"Error getting Gateway container: {e}")
            return None

    def get_status(self) -> GatewayStatus:
        """Get the current status of the Gateway container"""
        container = self._get_gateway_container()

        if container is None:
            return GatewayStatus(
                running=False,
                container_id=None,
                image=None,
                created_at=None,
                port=None
            )

        # Extract port from container configuration
        port = None
        if container.status == "running":
            # Check if using host networking
            network_mode = container.attrs.get("HostConfig", {}).get("NetworkMode", "")
            if network_mode == "host":
                # Host networking: Gateway uses port 15888 directly
                port = 15888
            else:
                # Bridge networking: Extract from port mappings
                ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                if "15888/tcp" in ports and ports["15888/tcp"]:
                    port = int(ports["15888/tcp"][0]["HostPort"])

        return GatewayStatus(
            running=container.status == "running",
            container_id=container.id,
            image=container.image.tags[0] if container.image.tags else container.image.id[:12],
            created_at=container.attrs.get("Created"),
            port=port
        )

    def start(self, config: GatewayConfig) -> Dict[str, Any]:
        """
        Start the Gateway container.
        If a container already exists, it will be stopped and removed before creating a new one.
        """
        # Check if Gateway is already running
        existing_container = self._get_gateway_container()
        if existing_container:
            if existing_container.status == "running":
                return {
                    "success": False,
                    "message": f"Gateway is already running. Use stop first or restart to update configuration."
                }
            else:
                # Remove stopped container
                logger.info("Removing stopped Gateway container")
                existing_container.remove(force=True)

        # Ensure directories exist
        dirs = self._ensure_gateway_directories()

        # SEC-048: the API only ever runs the Gateway secured (TLS + mTLS). There is no dev-mode
        # escape hatch — a Gateway holding wallet keys must never be served over plain HTTP.
        # A single secret (CONFIG_PASSWORD) secures the Gateway, this API, and deployed instances:
        # the Gateway uses GATEWAY_PASSPHRASE for both TLS and wallet encryption, and the shared
        # mTLS certs must be decryptable by this API's clients (which use CONFIG_PASSWORD), so the
        # passphrase is always CONFIG_PASSWORD.
        passphrase = settings.security.config_password

        # Set up volumes - bind-mount SOURCES must be HOST paths (the Docker daemon runs on the
        # host), so use the host-side base even though the API process wrote via the local base.
        host_base = self._gateway_base(host=True)
        volumes = {
            os.path.join(host_base, "conf"): {'bind': '/home/gateway/conf', 'mode': 'rw'},
            os.path.join(host_base, "logs"): {'bind': '/home/gateway/logs', 'mode': 'rw'},
        }

        # Run the Gateway with TLS + client-cert auth (DEV=false). Generate the shared mTLS cert
        # set once (idempotent; existing CA reused) into the local-base dir, and mount the matching
        # host path read-only so the Gateway decrypts its server key with the same passphrase.
        ensure_gateway_certs(passphrase, dirs["certs"])
        volumes[os.path.join(host_base, "certs")] = {'bind': self.GATEWAY_CERTS_BIND, 'mode': 'ro'}
        environment = {
            "GATEWAY_PASSPHRASE": passphrase,
            "DEV": "false",
        }
        logger.info("Starting Gateway in secured mode (TLS + mTLS, DEV=false)")

        # Configure logging
        log_config = LogConfig(
            type="json-file",
            config={
                'max-size': '10m',
                'max-file': "5",
            }
        )

        # Detect platform and configure networking
        # Native Linux: Use host networking (works natively)
        # Docker Desktop (macOS/Windows) or containerized: Use bridge networking
        system_platform = platform.system()

        # Check if running inside Docker container (Docker Desktop or containerized API)
        in_container = os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')

        # Only use host networking on native Linux (not inside a container)
        use_host_network = system_platform == "Linux" and not in_container

        if use_host_network:
            logger.info("Detected native Linux - using host network mode for Gateway")
        else:
            logger.info(f"Detected {system_platform} (in_container={in_container}) - using bridge networking for Gateway")

        try:
            # Build container configuration
            container_config = {
                "image": config.image,
                "name": self.GATEWAY_CONTAINER_NAME,
                "volumes": volumes,
                "environment": environment,
                "detach": True,
                "restart_policy": {"Name": "always"},
                "log_config": log_config,
            }

            if use_host_network:
                # Linux: Use host networking
                container_config["network_mode"] = "host"
            else:
                # macOS/Windows: Use bridge networking with port mapping.
                # SEC-048: bind the host publish to loopback so the socket is never offered on
                # the public interface (container-to-container traffic still flows over the
                # emqx-bridge network by container name). Host networking on Linux can't be
                # loopback-scoped here; mTLS is the control there.
                container_config["ports"] = {'15888/tcp': ('127.0.0.1', config.port)}

            container = self.client.containers.run(**container_config)

            # On macOS/Windows, connect to emqx-bridge network if it exists
            if not use_host_network:
                possible_networks = ["hummingbot-api_emqx-bridge", "emqx-bridge"]
                for net in possible_networks:
                    try:
                        network = self.client.networks.get(net)
                        network.connect(container)
                        logger.info(f"Connected Gateway to {net} network")
                        break
                    except docker.errors.NotFound:
                        continue

            logger.info(f"Gateway container started successfully: {container.id}")
            return {
                "success": True,
                "message": f"Gateway started successfully",
                "container_id": container.id,
                "port": config.port
            }

        except DockerException as e:
            logger.error(f"Failed to start Gateway container: {e}")
            return {
                "success": False,
                "message": f"Failed to start Gateway: {str(e)}"
            }

    def stop(self) -> Dict[str, Any]:
        """Stop the Gateway container"""
        container = self._get_gateway_container()

        if container is None:
            return {
                "success": False,
                "message": "Gateway container not found"
            }

        try:
            if container.status == "running":
                container.stop()
                logger.info("Gateway container stopped")
            return {
                "success": True,
                "message": "Gateway stopped successfully"
            }
        except DockerException as e:
            logger.error(f"Failed to stop Gateway container: {e}")
            return {
                "success": False,
                "message": f"Failed to stop Gateway: {str(e)}"
            }

    def restart(self, config: Optional[GatewayConfig] = None) -> Dict[str, Any]:
        """
        Restart the Gateway container.
        If config is provided, the container will be recreated with the new configuration.
        """
        container = self._get_gateway_container()

        if container is None:
            if config:
                # No existing container, just start with new config
                return self.start(config)
            else:
                return {
                    "success": False,
                    "message": "Gateway container not found. Use start with configuration to create one."
                }

        if config:
            # Stop and remove existing container, then start with new config
            try:
                container.remove(force=True)
                logger.info("Removed existing Gateway container for restart with new config")
            except DockerException as e:
                logger.error(f"Failed to remove Gateway container: {e}")
                return {
                    "success": False,
                    "message": f"Failed to remove existing container: {str(e)}"
                }
            return self.start(config)
        else:
            # Simple restart of existing container
            try:
                container.restart()
                logger.info("Gateway container restarted")
                return {
                    "success": True,
                    "message": "Gateway restarted successfully"
                }
            except DockerException as e:
                logger.error(f"Failed to restart Gateway container: {e}")
                return {
                    "success": False,
                    "message": f"Failed to restart Gateway: {str(e)}"
                }

    def remove(self, remove_data: bool = False) -> Dict[str, Any]:
        """
        Remove the Gateway container and optionally its data.

        Args:
            remove_data: If True, also remove the gateway-files directory
        """
        container = self._get_gateway_container()

        if container is None:
            if remove_data:
                # No container, but try to remove data if requested
                gateway_dir = self._gateway_base(host=False)
                if os.path.exists(gateway_dir):
                    try:
                        shutil.rmtree(gateway_dir)
                        logger.info(f"Removed Gateway data directory: {gateway_dir}")
                        return {
                            "success": True,
                            "message": "Gateway data removed (no container was found)"
                        }
                    except Exception as e:
                        logger.error(f"Failed to remove Gateway data: {e}")
                        return {
                            "success": False,
                            "message": f"Failed to remove Gateway data: {str(e)}"
                        }
            return {
                "success": False,
                "message": "Gateway container not found"
            }

        try:
            # Remove container
            container.remove(force=True)
            logger.info("Gateway container removed")

            # Remove data if requested
            if remove_data:
                gateway_dir = self._gateway_base(host=False)
                if os.path.exists(gateway_dir):
                    shutil.rmtree(gateway_dir)
                    logger.info(f"Removed Gateway data directory: {gateway_dir}")
                    return {
                        "success": True,
                        "message": "Gateway container and data removed successfully"
                    }

            return {
                "success": True,
                "message": "Gateway container removed successfully"
            }

        except DockerException as e:
            logger.error(f"Failed to remove Gateway container: {e}")
            return {
                "success": False,
                "message": f"Failed to remove Gateway: {str(e)}"
            }
        except Exception as e:
            logger.error(f"Failed to remove Gateway data: {e}")
            return {
                "success": False,
                "message": f"Gateway container removed but failed to remove data: {str(e)}"
            }

    def get_logs(self, tail: int = 100) -> Dict[str, Any]:
        """Get logs from the Gateway container"""
        container = self._get_gateway_container()

        if container is None:
            return {
                "success": False,
                "message": "Gateway container not found"
            }

        try:
            logs = container.logs(tail=tail, timestamps=True).decode('utf-8')
            return {
                "success": True,
                "logs": logs
            }
        except DockerException as e:
            logger.error(f"Failed to get Gateway logs: {e}")
            return {
                "success": False,
                "message": f"Failed to get logs: {str(e)}"
            }
