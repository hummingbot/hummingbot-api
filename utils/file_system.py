import importlib
import inspect
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Type

import yaml
from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.strategy_v2.controllers.controller_base import ControllerConfigBase
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import DirectionalTradingControllerConfigBase
from hummingbot.strategy_v2.controllers.market_making_controller_base import MarketMakingControllerConfigBase

# Create module-specific logger
logger = logging.getLogger(__name__)


class FileSystemUtil:
    """
    FileSystemUtil provides utility functions for file and directory management,
    as well as dynamic loading of script configurations.

    All file operations are performed relative to the base_path unless an absolute path is provided.
    Implements singleton pattern to ensure the same instance is reused.
    """
    _instance = None
    base_path: str = "bots"  # Default base path

    def __new__(cls, base_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super(FileSystemUtil, cls).__new__(cls)
            cls._instance.base_path = base_path if base_path else "bots"
        return cls._instance

    def __init__(self, base_path: Optional[str] = None):
        """
        Initializes the FileSystemUtil with a base path.
        :param base_path: The base directory path for file operations.
        """
        # Singleton pattern - instance already configured in __new__
        pass

    def _get_full_path(self, path: str) -> str:
        """
        Get the full path by combining base_path with relative path.
        :param path: Relative or absolute path.
        :return: Full absolute path.
        """
        return path if os.path.isabs(path) else os.path.join(self.base_path, path)

    def list_files(self, directory: str) -> List[str]:
        """
        Lists all files in a given directory.
        :param directory: The directory to list files from.
        :return: List of file names in the directory.
        :raises FileNotFoundError: If the directory does not exist.
        :raises PermissionError: If access is denied to the directory.
        """
        excluded_files = ["__init__.py", "__pycache__", ".DS_Store", ".dockerignore", ".gitignore"]
        dir_path = self._get_full_path(directory)
        if not os.path.exists(dir_path):
            raise FileNotFoundError(f"Directory '{directory}' not found")
        if not os.path.isdir(dir_path):
            raise NotADirectoryError(f"Path '{directory}' is not a directory")
        return [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f)) and f not in excluded_files]

    def list_folders(self, directory: str) -> List[str]:
        """
        Lists all folders in a given directory.
        :param directory: The directory to list folders from.
        :return: List of folder names in the directory.
        :raises FileNotFoundError: If the directory does not exist.
        :raises PermissionError: If access is denied to the directory.
        """
        dir_path = self._get_full_path(directory)
        if not os.path.exists(dir_path):
            raise FileNotFoundError(f"Directory '{directory}' not found")
        if not os.path.isdir(dir_path):
            raise NotADirectoryError(f"Path '{directory}' is not a directory")
        return [d for d in os.listdir(dir_path) if os.path.isdir(os.path.join(dir_path, d))]

    def create_folder(self, directory: str, folder_name: str) -> None:
        """
        Creates a folder in a specified directory.
        :param directory: The directory to create the folder in.
        :param folder_name: The name of the folder to be created.
        :raises PermissionError: If permission is denied to create the folder.
        :raises OSError: If there's an OS-level error creating the folder.
        """
        if not folder_name or '/' in folder_name or '\\' in folder_name:
            raise ValueError(f"Invalid folder name: '{folder_name}'")
        folder_path = self._get_full_path(os.path.join(directory, folder_name))
        os.makedirs(folder_path, exist_ok=True)

    def copy_folder(self, src: str, dest: str) -> None:
        """
        Copies a folder to a new location.
        :param src: The source folder to copy.
        :param dest: The destination folder to copy to.
        :raises FileNotFoundError: If source folder doesn't exist.
        :raises PermissionError: If permission is denied.
        """
        src_path = self._get_full_path(src)
        dest_path = self._get_full_path(dest)

        if not os.path.exists(src_path):
            raise FileNotFoundError(f"Source folder '{src}' not found")
        if not os.path.isdir(src_path):
            raise NotADirectoryError(f"Source path '{src}' is not a directory")

        shutil.copytree(src_path, dest_path, dirs_exist_ok=True)

    def copy_file(self, src: str, dest: str) -> None:
        """
        Copies a file to a new location.
        :param src: The source file to copy.
        :param dest: The destination file to copy to.
        :raises FileNotFoundError: If source file doesn't exist.
        :raises PermissionError: If permission is denied.
        """
        src_path = self._get_full_path(src)
        dest_path = self._get_full_path(dest)

        if not os.path.exists(src_path):
            raise FileNotFoundError(f"Source file '{src}' not found")
        if os.path.isdir(src_path):
            raise IsADirectoryError(f"Source path '{src}' is a directory, not a file")

        # Ensure destination directory exists
        dest_dir = os.path.dirname(dest_path)
        os.makedirs(dest_dir, exist_ok=True)

        shutil.copy2(src_path, dest_path)

    def delete_folder(self, directory: str, folder_name: str) -> None:
        """
        Deletes a folder in a specified directory.
        :param directory: The directory to delete the folder from.
        :param folder_name: The name of the folder to be deleted.
        :raises FileNotFoundError: If folder doesn't exist.
        :raises PermissionError: If permission is denied.
        """
        folder_path = self._get_full_path(os.path.join(directory, folder_name))
        if not os.path.exists(folder_path):
            raise FileNotFoundError(f"Folder '{folder_name}' not found in '{directory}'")
        if not os.path.isdir(folder_path):
            raise NotADirectoryError(f"Path '{folder_name}' is not a directory")
        shutil.rmtree(folder_path)

    def delete_file(self, directory: str, file_name: str) -> None:
        """
        Deletes a file in a specified directory.
        :param directory: The directory to delete the file from.
        :param file_name: The name of the file to be deleted.
        :raises FileNotFoundError: If file doesn't exist.
        :raises PermissionError: If permission is denied.
        """
        file_path = self._get_full_path(os.path.join(directory, file_name))
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File '{file_name}' not found in '{directory}'")
        if os.path.isdir(file_path):
            raise IsADirectoryError(f"Path '{file_name}' is a directory, not a file")
        os.remove(file_path)

    def path_exists(self, path: str) -> bool:
        """
        Checks if a path exists.
        :param path: The path to check.
        :return: True if the path exists, False otherwise.
        """
        return os.path.exists(self._get_full_path(path))

    def add_file(self, directory: str, file_name: str, content: str, override: bool = False) -> None:
        """
        Adds a file to a specified directory.
        :param directory: The directory to add the file to.
        :param file_name: The name of the file to be added.
        :param content: The content to be written to the file.
        :param override: If True, override the file if it exists.
        :raises ValueError: If file_name is invalid.
        :raises FileExistsError: If file exists and override is False.
        :raises PermissionError: If permission is denied to write the file.
        """
        if not file_name or '/' in file_name or '\\' in file_name:
            raise ValueError(f"Invalid file name: '{file_name}'")

        dir_path = self._get_full_path(directory)
        os.makedirs(dir_path, exist_ok=True)

        file_path = os.path.join(dir_path, file_name)
        if not override and os.path.exists(file_path):
            raise FileExistsError(f"File '{file_name}' already exists in '{directory}'.")

        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(content)

    def append_to_file(self, directory: str, file_name: str, content: str) -> None:
        """
        Appends content to a specified file.
        :param directory: The directory containing the file.
        :param file_name: The name of the file to append to.
        :param content: The content to append to the file.
        :raises FileNotFoundError: If file doesn't exist.
        :raises PermissionError: If permission is denied.
        """
        file_path = self._get_full_path(os.path.join(directory, file_name))
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File '{file_name}' not found in '{directory}'")
        if os.path.isdir(file_path):
            raise IsADirectoryError(f"Path '{file_name}' is a directory, not a file")

        with open(file_path, 'a', encoding='utf-8') as file:
            file.write(content)

    def read_file(self, file_path: str) -> str:
        """
        Reads the content of a file.
        :param file_path: The relative path to the file from base_path.
        :return: The content of the file as a string.
        :raises FileNotFoundError: If the file does not exist.
        :raises PermissionError: If access is denied to the file.
        :raises IsADirectoryError: If the path points to a directory.
        """
        full_path = self._get_full_path(file_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File '{file_path}' not found")
        if os.path.isdir(full_path):
            raise IsADirectoryError(f"Path '{file_path}' is a directory, not a file")

        with open(full_path, 'r', encoding='utf-8') as file:
            return file.read()

    def dump_dict_to_yaml(self, filename: str, data_dict: dict) -> None:
        """
        Dumps a dictionary to a YAML file.
        :param filename: The file to dump the dictionary into (relative to base_path).
        :param data_dict: The dictionary to dump.
        :raises PermissionError: If permission is denied to write the file.
        """
        file_path = self._get_full_path(filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as file:
            yaml.dump(data_dict, file, default_flow_style=False, allow_unicode=True)

    def read_yaml_file(self, file_path: str) -> dict:
        """
        Reads a YAML file and returns the data as a dictionary.
        :param file_path: The path to the YAML file (relative to base_path or absolute).
        :return: Dictionary containing the YAML file data.
        :raises FileNotFoundError: If the file doesn't exist.
        :raises yaml.YAMLError: If the YAML is invalid.
        """
        full_path = self._get_full_path(file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"YAML file '{file_path}' not found")

        with open(full_path, 'r', encoding='utf-8') as file:
            try:
                data = yaml.safe_load(file)
                return data if data is not None else {}
            except yaml.YAMLError as e:
                raise yaml.YAMLError(f"Invalid YAML in file '{file_path}': {e}")

    @staticmethod
    def load_script_config_class(script_name: str) -> Optional[Type[BaseClientModel]]:
        """
        Dynamically loads a script's configuration class.
        :param script_name: The name of the script file (without the '.py' extension).
        :return: The configuration class from the script, or None if not found.
        """
        try:
            # Assuming scripts are in a package named 'scripts'
            module_name = f"bots.scripts.{script_name.replace('.py', '')}"
            if module_name not in sys.modules:
                script_module = importlib.import_module(module_name)
            else:
                script_module = importlib.reload(sys.modules[module_name])

            # Find the subclass of BaseClientModel in the module
            for _, cls in inspect.getmembers(script_module, inspect.isclass):
                if issubclass(cls, BaseClientModel) and cls is not BaseClientModel:
                    return cls
        except (ImportError, AttributeError, ModuleNotFoundError) as e:
            logger.warning(f"Error loading script class for '{script_name}': {e}")
        return None

    @staticmethod
    def load_controller_config_class(controller_type: str, controller_name: str) -> Optional[Type]:
        """
        Dynamically loads a controller's configuration class.
        Supports both single-file controllers (controller.py) and
        package-style controllers (controller/controller.py).
        :param controller_type: The type of the controller.
        :param controller_name: The name of the controller file (without the '.py' extension).
        :return: The configuration class from the controller, or None if not found.
        """
        controller_name = controller_name.replace('.py', '')

        # Try single-file first: bots.controllers.{type}.{name}
        # Then package-style: bots.controllers.{type}.{name}.{name}
        module_paths = [
            f"bots.controllers.{controller_type}.{controller_name}",
            f"bots.controllers.{controller_type}.{controller_name}.{controller_name}",
        ]

        for module_name in module_paths:
            try:
                if module_name not in sys.modules:
                    script_module = importlib.import_module(module_name)
                else:
                    script_module = importlib.reload(sys.modules[module_name])

                # Find the subclass of controller config base in the module
                for _, cls in inspect.getmembers(script_module, inspect.isclass):
                    is_directional = (issubclass(cls, DirectionalTradingControllerConfigBase)
                                      and cls is not DirectionalTradingControllerConfigBase)
                    is_market_making = (issubclass(cls, MarketMakingControllerConfigBase)
                                        and cls is not MarketMakingControllerConfigBase)
                    is_generic = (issubclass(cls, ControllerConfigBase)
                                  and cls is not ControllerConfigBase)
                    if is_directional or is_market_making or is_generic:
                        return cls
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue

        logger.warning(f"Could not load controller class for '{controller_type}.{controller_name}'")
        return None

    def ensure_file_and_dump_text(self, file_path: str, text: str) -> None:
        """
        Ensures that the directory for the file exists, then writes text to a file.
        :param file_path: The file path to write to (relative to base_path or absolute).
        :param text: The text to write.
        :raises PermissionError: If permission is denied.
        """
        full_path = self._get_full_path(file_path) if not os.path.isabs(file_path) else file_path
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding='utf-8') as f:
            f.write(text)

    def get_connector_keys_path(self, account_name: str, connector_name: str) -> Path:
        """
        Get the path to connector credentials file.
        :param account_name: Name of the account.
        :param connector_name: Name of the connector.
        :return: Path to the connector credentials file.
        """
        return Path("credentials") / account_name / "connectors" / f"{connector_name}.yml"

    def save_model_to_yml(self, yml_path: str, cm: ClientConfigAdapter) -> None:
        """
        Save a ClientConfigAdapter model to a YAML file.
        :param yml_path: Path to the YAML file (relative to base_path or absolute).
        :param cm: The ClientConfigAdapter to save.
        :raises PermissionError: If permission is denied to write the file.
        """
        try:
            full_path = self._get_full_path(yml_path)
            cm_yml_str = cm.generate_yml_output_str_with_comments()
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as outfile:
                outfile.write(cm_yml_str)
        except Exception as e:
            logger.error(f"Error writing configs to '{yml_path}': {e}", exc_info=True)
            raise

    def get_base_path(self) -> str:
        """
        Returns the base path for file operations
        :return: The base path string
        """
        return self.base_path

    def get_directory_creation_time(self, path):
        """
        Get the creation time of a directory
        :param path: The path to the directory
        :return: ISO formatted creation time string or None if directory doesn't exist
        """
        import datetime
        import os

        full_path = self._get_full_path(path)
        if not os.path.exists(full_path):
            return None

        # Get creation time (platform dependent)
        try:
            # For Unix systems, use stat
            creation_time = os.stat(full_path).st_ctime
            # Convert to datetime
            return datetime.datetime.fromtimestamp(creation_time).isoformat()
        except Exception:
            # Fallback
            return "unknown"

    def list_directories(self, path):
        """
        List all directories within a given path
        :param path: The path to list directories from
        :return: List of directory names
        """
        import os

        full_path = self._get_full_path(path)
        if not os.path.exists(full_path):
            return []

        try:
            # Return only directories
            return [d for d in os.listdir(full_path) if os.path.isdir(os.path.join(full_path, d))]
        except Exception:
            return []

    def list_databases(self) -> List[str]:
        """
        Lists all database files in archived instances
        :return: List of database file paths
        """
        try:
            archived_instances = self.list_folders("archived")
        except FileNotFoundError:
            return []

        archived_databases = []
        for archived_instance in archived_instances:
            db_path = self._get_full_path(os.path.join("archived", archived_instance, "data"))
            try:
                if os.path.exists(db_path):
                    archived_databases.extend([
                        os.path.join(db_path, db_file)
                        for db_file in os.listdir(db_path)
                        if db_file.endswith(".sqlite")
                    ])
            except (OSError, PermissionError) as e:
                logger.warning(f"Error accessing database path '{db_path}': {e}")
        return archived_databases

    def list_checkpoints(self, full_path: bool = False) -> List[str]:
        """
        Lists all checkpoint database files
        :param full_path: If True, return full paths, otherwise just filenames
        :return: List of checkpoint database files
        """
        dir_path = self._get_full_path("data")
        if not os.path.exists(dir_path):
            return []

        try:
            files = os.listdir(dir_path)
            checkpoint_files = [
                f for f in files
                if (os.path.isfile(os.path.join(dir_path, f))
                    and f.startswith("checkpoint")
                    and f.endswith(".sqlite"))
            ]

            if full_path:
                return [os.path.join(dir_path, f) for f in checkpoint_files]
            else:
                return checkpoint_files
        except (OSError, PermissionError) as e:
            logger.warning(f"Error listing checkpoints in '{dir_path}': {e}")
            return []


fs_util = FileSystemUtil()
