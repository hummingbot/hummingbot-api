import asyncio
import logging
from typing import Optional
import re

import docker

from utils.mqtt_manager import MQTTManager

logger = logging.getLogger(__name__)


# HummingbotPerformanceListener class is no longer needed
# All functionality is now handled by MQTTManager


class BotsOrchestrator:
    """Orchestrates Hummingbot instances using Docker and MQTT communication."""

    def __init__(self, broker_host, broker_port, broker_username, broker_password):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.broker_username = broker_username
        self.broker_password = broker_password

        # Initialize Docker client
        self.docker_client = docker.from_env()

        # Initialize MQTT manager
        self.mqtt_manager = MQTTManager(host=broker_host, port=broker_port, username=broker_username, password=broker_password)

        # Active bots tracking
        self.active_bots = {}
        self._update_bots_task: Optional[asyncio.Task] = None
        
        # Track bots that are currently being stopped and archived
        self.stopping_bots = set()

        # MQTT manager will be started asynchronously later

    @staticmethod
    def hummingbot_containers_fiter(container):
        """Filter for Hummingbot containers based on image name pattern."""
        try:
            # Get the image name (first tag if available, otherwise the image ID)
            image_name = container.image.tags[0] if container.image.tags else str(container.image)
            pattern = r'.+/hummingbot:'
            return bool(re.match(pattern, image_name))
        except Exception:
            return False

    async def get_active_containers(self):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_active_containers)

    def _sync_get_active_containers(self):
        return [
            container.name
            for container in self.docker_client.containers.list()
            if container.status == "running"
        ]

    def start(self):
        """Start the loop that monitors active bots."""
        # Start MQTT manager and update loop in async context
        self._update_bots_task = asyncio.create_task(self._start_async())

    async def _start_async(self):
        """Start MQTT manager and update loop asynchronously."""
        logger.info("Starting MQTT manager...")
        await self.mqtt_manager.start()

        # Then start the update loop
        await self.update_active_bots()

    def stop(self):
        """Stop the active bots monitoring loop."""
        if self._update_bots_task:
            self._update_bots_task.cancel()
        self._update_bots_task = None

        # Stop MQTT manager asynchronously
        asyncio.create_task(self.mqtt_manager.stop())

    async def update_active_bots(self, sleep_time=1.0):
        """Monitor and update active bots list using both Docker and MQTT discovery."""
        while True:
            try:
                # Get bots from Docker containers
                docker_bots = await self.get_active_containers()

                # Get bots from MQTT messages (auto-discovered)
                mqtt_bots = self.mqtt_manager.get_discovered_bots(timeout_seconds=30)  # 30 second timeout

                # Combine both sources
                all_active_bots = set([bot for bot in docker_bots + mqtt_bots if not self.is_bot_stopping(bot)])

                # Remove bots that are no longer active
                for bot_name in list(self.active_bots):
                    if bot_name not in all_active_bots:
                        self.mqtt_manager.clear_bot_data(bot_name)
                        del self.active_bots[bot_name]

                # Add new bots
                for bot_name in all_active_bots:
                    if bot_name not in self.active_bots:
                        self.active_bots[bot_name] = {
                            "bot_name": bot_name,
                            "status": "connected",
                            "source": "docker" if bot_name in docker_bots else "mqtt",
                        }
                        # Subscribe to this specific bot's topics
                        await self.mqtt_manager.subscribe_to_bot(bot_name)

            except Exception as e:
                logger.error(f"Error in update_active_bots: {e}", exc_info=True)

            await asyncio.sleep(sleep_time)

    # Interact with a specific bot
    async def start_bot(self, bot_name, **kwargs):
        """
        Start a bot with optional script.
        Maintains backward compatibility with kwargs.
        """
        if bot_name not in self.active_bots:
            logger.warning(f"Bot {bot_name} not found in active bots")
            return {"success": False, "message": f"Bot {bot_name} not found"}

        # Create StartCommandMessage.Request format
        data = {
            "log_level": kwargs.get("log_level"),
            "script": kwargs.get("script"),
            "conf": kwargs.get("conf"),
            "is_quickstart": kwargs.get("is_quickstart", False),
            "async_backend": kwargs.get("async_backend", True),
        }

        success = await self.mqtt_manager.publish_command(bot_name, "start", data)
        return {"success": success}

    async def stop_bot(self, bot_name, **kwargs):
        """
        Stop a bot.
        Maintains backward compatibility with kwargs.
        """
        if bot_name not in self.active_bots:
            logger.warning(f"Bot {bot_name} not found in active bots")
            return {"success": False, "message": f"Bot {bot_name} not found"}

        # Create StopCommandMessage.Request format
        data = {
            "skip_order_cancellation": kwargs.get("skip_order_cancellation", False),
            "async_backend": kwargs.get("async_backend", True),
        }

        success = await self.mqtt_manager.publish_command(bot_name, "stop", data)

        # Clear performance data after stop command to immediately reflect stopped status
        if success:
            self.mqtt_manager.clear_bot_performance(bot_name)

        return {"success": success}

    async def import_strategy_for_bot(self, bot_name, strategy, **kwargs):
        """
        Import a strategy configuration for a bot.
        Maintains backward compatibility.
        """
        if bot_name not in self.active_bots:
            logger.warning(f"Bot {bot_name} not found in active bots")
            return {"success": False, "message": f"Bot {bot_name} not found"}

        # Create ImportCommandMessage.Request format
        data = {"strategy": strategy}
        success = await self.mqtt_manager.publish_command(bot_name, "import_strategy", data)
        return {"success": success}

    async def configure_bot(self, bot_name, params, **kwargs):
        """
        Configure bot parameters.
        Maintains backward compatibility.
        """
        if bot_name not in self.active_bots:
            logger.warning(f"Bot {bot_name} not found in active bots")
            return {"success": False, "message": f"Bot {bot_name} not found"}

        # Create ConfigCommandMessage.Request format
        data = {"params": params}
        success = await self.mqtt_manager.publish_command(bot_name, "config", data)
        return {"success": success}

    async def get_bot_history(self, bot_name, **kwargs):
        """
        Request bot trading history and wait for the response.
        Maintains backward compatibility.
        """
        if bot_name not in self.active_bots:
            logger.warning(f"Bot {bot_name} not found in active bots")
            return {"success": False, "message": f"Bot {bot_name} not found"}

        # Create HistoryCommandMessage.Request format
        data = {
            "days": kwargs.get("days", 0),
            "verbose": kwargs.get("verbose", False),
            "precision": kwargs.get("precision"),
            "async_backend": kwargs.get("async_backend", False),
        }

        # Use the new RPC method to wait for response
        timeout = kwargs.get("timeout", 30.0)  # Default 30 second timeout
        response = await self.mqtt_manager.publish_command_and_wait(bot_name, "history", data, timeout=timeout)

        if response is None:
            return {
                "success": False,
                "message": f"No response received from {bot_name} within {timeout} seconds",
                "timeout": True,
            }

        return {"success": True, "data": response}

    @staticmethod
    def determine_controller_performance(controllers_performance):
        cleaned_performance = {}
        for controller, performance in controllers_performance.items():
            try:
                # Check if all the metrics are numeric
                _ = sum(metric for key, metric in performance.items() if key not in ("positions_summary", "close_type_counts"))
                cleaned_performance[controller] = {"status": "running", "performance": performance}
            except Exception as e:
                cleaned_performance[controller] = {
                    "status": "error",
                    "error": f"Some metrics are not numeric, check logs and restart controller: {e}",
                }
        return cleaned_performance

    def get_all_bots_status(self):
        # TODO: improve logic of bots state management
        """Get status information for all active bots."""
        all_bots_status = {}
        for bot in [bot for bot in self.active_bots if not self.is_bot_stopping(bot)]:
            status = self.get_bot_status(bot)
            status["source"] = self.active_bots[bot].get("source", "unknown")
            all_bots_status[bot] = status
        return all_bots_status

    def get_bot_status(self, bot_name):
        """
        Get status information for a specific bot.
        """
        if bot_name not in self.active_bots:
            return {"status": "not_found", "error": f"Bot {bot_name} not found"}

        try:
            # Check if bot is currently being stopped and archived
            if bot_name in self.stopping_bots:
                return {
                    "status": "stopping",
                    "message": "Bot is currently being stopped and archived",
                    "performance": {},
                    "error_logs": [],
                    "general_logs": [],
                    "recently_active": False,
                }
            
            # Get data from MQTT manager
            controllers_performance = self.mqtt_manager.get_bot_performance(bot_name)
            performance = self.determine_controller_performance(controllers_performance)
            error_logs = self.mqtt_manager.get_bot_error_logs(bot_name)
            general_logs = self.mqtt_manager.get_bot_logs(bot_name)

            # Check if bot has sent recent messages (within last 30 seconds)
            discovered_bots = self.mqtt_manager.get_discovered_bots(timeout_seconds=30)
            recently_active = bot_name in discovered_bots

            # Determine status based on performance data and recent activity
            if len(performance) > 0 and recently_active:
                status = "running"
            elif len(performance) > 0 and not recently_active:
                status = "idle"  # Has performance data but no recent activity
            else:
                status = "stopped"

            return {
                "status": status,
                "performance": performance,
                "error_logs": error_logs,
                "general_logs": general_logs,
                "recently_active": recently_active,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def set_bot_stopping(self, bot_name: str):
        """Mark a bot as currently being stopped and archived."""
        self.stopping_bots.add(bot_name)
        logger.info(f"Marked bot {bot_name} as stopping")
    
    def clear_bot_stopping(self, bot_name: str):
        """Clear the stopping status for a bot."""
        self.stopping_bots.discard(bot_name)
        logger.info(f"Cleared stopping status for bot {bot_name}")
    
    def is_bot_stopping(self, bot_name: str) -> bool:
        """Check if a bot is currently being stopped."""
        return bot_name in self.stopping_bots
    
