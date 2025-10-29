import logging
import os
import shutil
from typing import Optional, Dict

import docker
from docker.errors import DockerException
from docker.types import LogConfig

from models.gateway import GatewayConfig, GatewayStatus

# Create module-specific logger
logger = logging.getLogger(__name__)


class GatewayService:
    """
    Service for managing the Hummingbot Gateway Docker container.
    Ensures only one Gateway instance can exist at a time.
    """

    GATEWAY_CONTAINER_NAME = "gateway"
    GATEWAY_DIR = "gateway-files"

    def __init__(self):
        self.SOURCE_PATH = os.getcwd()
        # Use BOTS_PATH if set (for Docker), otherwise use SOURCE_PATH (for local)
        self.BOTS_PATH = os.environ.get('BOTS_PATH', self.SOURCE_PATH)
        try:
            self.client = docker.from_env()
        except DockerException as e:
            logger.error(f"Failed to connect to Docker. Error: {e}")
            raise

    def _ensure_gateway_directories(self):
        """Create necessary directories for Gateway if they don't exist"""
        # Gateway files are at root level, same as bots directory
        gateway_base = os.path.join(self.BOTS_PATH, self.GATEWAY_DIR)

        conf_dir = os.path.join(gateway_base, "conf")
        logs_dir = os.path.join(gateway_base, "logs")

        os.makedirs(conf_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)

        return {
            "base": gateway_base,
            "conf": conf_dir,
            "logs": logs_dir
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
        if container.attrs.get("NetworkSettings", {}).get("Ports"):
            ports = container.attrs["NetworkSettings"]["Ports"]
            # Gateway typically uses 15888
            if "15888/tcp" in ports and ports["15888/tcp"]:
                port = int(ports["15888/tcp"][0]["HostPort"])

        return GatewayStatus(
            running=container.status == "running",
            container_id=container.id,
            image=container.image.tags[0] if container.image.tags else container.image.id[:12],
            created_at=container.attrs.get("Created"),
            port=port
        )

    def start(self, config: GatewayConfig) -> Dict[str, any]:
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

        # Set up volumes - use BOTS_PATH which contains the HOST path
        volumes = {
            os.path.join(self.BOTS_PATH, self.GATEWAY_DIR, "conf"): {'bind': '/home/gateway/conf', 'mode': 'rw'},
            os.path.join(self.BOTS_PATH, self.GATEWAY_DIR, "logs"): {'bind': '/home/gateway/logs', 'mode': 'rw'},
        }

        # Set up environment variables
        environment = {
            "GATEWAY_PASSPHRASE": config.passphrase,
            "DEV": str(config.dev_mode).lower(),
        }

        # Set up port mapping
        ports = {
            '15888/tcp': config.port
        }

        # Configure logging
        log_config = LogConfig(
            type="json-file",
            config={
                'max-size': '10m',
                'max-file': "5",
            }
        )

        # Connect to the same Docker network as the API if it exists
        # This allows the API container to communicate with Gateway using container name
        # Try multiple network names (docker-compose prefixes with project name)
        possible_networks = ["hummingbot-api_emqx-bridge", "emqx-bridge"]
        network_name = None

        for net in possible_networks:
            try:
                # Check if the network exists
                self.client.networks.get(net)
                network_name = net
                logger.info(f"Will connect Gateway to existing network: {network_name}")
                break
            except docker.errors.NotFound:
                continue

        if not network_name:
            # Network doesn't exist, likely running outside Docker
            logger.info("Docker network 'emqx-bridge' not found, Gateway will use default networking")

        try:
            container = self.client.containers.run(
                image=config.image,
                name=self.GATEWAY_CONTAINER_NAME,
                volumes=volumes,
                environment=environment,
                ports=ports,
                detach=True,
                restart_policy={"Name": "always"},
                log_config=log_config,
            )

            # Connect to the emqx-bridge network if it exists
            if network_name:
                try:
                    network = self.client.networks.get(network_name)
                    network.connect(container)
                    logger.info(f"Connected Gateway container to {network_name} network")
                except Exception as e:
                    logger.warning(f"Failed to connect Gateway to {network_name} network: {e}")

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

    def stop(self) -> Dict[str, any]:
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

    def restart(self, config: Optional[GatewayConfig] = None) -> Dict[str, any]:
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

    def remove(self, remove_data: bool = False) -> Dict[str, any]:
        """
        Remove the Gateway container and optionally its data.

        Args:
            remove_data: If True, also remove the gateway-files directory
        """
        container = self._get_gateway_container()

        if container is None:
            if remove_data:
                # No container, but try to remove data if requested
                gateway_dir = os.path.join(self.SOURCE_PATH, self.GATEWAY_DIR)
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
                gateway_dir = os.path.join(self.SOURCE_PATH, self.GATEWAY_DIR)
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

    def get_logs(self, tail: int = 100) -> Dict[str, any]:
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
