import json
from typing import Dict, List

import yaml
from fastapi import APIRouter, HTTPException
from starlette import status

from models import Controller, ControllerType
from utils.file_system import fs_util

router = APIRouter(tags=["Controllers"], prefix="/controllers")


@router.get("", response_model=Dict[str, List[str]])
async def list_controllers():
    """
    List all controllers organized by type.
    
    Returns:
        Dictionary mapping controller types to lists of controller names
    """
    result = {}
    for controller_type in ControllerType:
        try:
            files = fs_util.list_files(f'controllers/{controller_type.value}')
            result[controller_type.value] = [
                f.replace('.py', '') for f in files 
                if f.endswith('.py') and f != "__init__.py"
            ]
        except FileNotFoundError:
            result[controller_type.value] = []
    return result


# Controller Configuration endpoints (must come before controller type routes)
@router.get("/configs", response_model=List[Dict])
async def list_controller_configs():
    """
    List all controller configurations with metadata.
    
    Returns:
        List of controller configuration objects with name, controller_name, controller_type, and other metadata
    """
    try:
        config_files = [f for f in fs_util.list_files('conf/controllers') if f.endswith('.yml')]
        configs = []
        
        for config_file in config_files:
            config_name = config_file.replace('.yml', '')
            try:
                config = fs_util.read_yaml_file(f"conf/controllers/{config_file}")
                configs.append(config)
            except Exception as e:
                # If config is malformed, still include it with basic info
                configs.append({
                    "id": config_name,
                    "controller_name": "error",
                    "controller_type": "error", 
                    "error": str(e)
                })
        
        return configs
    except FileNotFoundError:
        return []


@router.get("/configs/{config_name}", response_model=Dict)
async def get_controller_config(config_name: str):
    """
    Get controller configuration by config name.
    
    Args:
        config_name: Name of the configuration file to retrieve
        
    Returns:
        Dictionary with controller configuration
        
    Raises:
        HTTPException: 404 if configuration not found
    """
    try:
        config = fs_util.read_yaml_file(f"conf/controllers/{config_name}.yml")
        return config
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Configuration '{config_name}' not found")


@router.post("/configs/{config_name}", status_code=status.HTTP_201_CREATED)
async def create_or_update_controller_config(config_name: str, config: Dict):
    """
    Create or update controller configuration.
    
    Args:
        config_name: Name of the configuration file
        config: Configuration dictionary to save
        
    Returns:
        Success message when configuration is saved
        
    Raises:
        HTTPException: 400 if save error occurs
    """
    try:
        yaml_content = yaml.dump(config, default_flow_style=False)
        fs_util.add_file('conf/controllers', f"{config_name}.yml", yaml_content, override=True)
        return {"message": f"Configuration '{config_name}' saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/configs/{config_name}")
async def delete_controller_config(config_name: str):
    """
    Delete controller configuration.
    
    Args:
        config_name: Name of the configuration file to delete
        
    Returns:
        Success message when configuration is deleted
        
    Raises:
        HTTPException: 404 if configuration not found
    """
    try:
        fs_util.delete_file('conf/controllers', f"{config_name}.yml")
        return {"message": f"Configuration '{config_name}' deleted successfully"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Configuration '{config_name}' not found")


@router.get("/{controller_type}/{controller_name}", response_model=Dict[str, str])
async def get_controller(controller_type: ControllerType, controller_name: str):
    """
    Get controller content by type and name.
    
    Args:
        controller_type: Type of the controller
        controller_name: Name of the controller
        
    Returns:
        Dictionary with controller name, type, and content
        
    Raises:
        HTTPException: 404 if controller not found
    """
    try:
        content = fs_util.read_file(f"controllers/{controller_type.value}/{controller_name}.py")
        return {
            "name": controller_name,
            "type": controller_type.value,
            "content": content
        }
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, 
            detail=f"Controller '{controller_name}' not found in '{controller_type.value}'"
        )


@router.post("/{controller_type}/{controller_name}", status_code=status.HTTP_201_CREATED)
async def create_or_update_controller(controller_type: ControllerType, controller_name: str, controller: Controller):
    """
    Create or update a controller.
    
    Args:
        controller_type: Type of controller to create/update
        controller_name: Name of the controller (from URL path)
        controller: Controller object with content (and optional type for validation)
        
    Returns:
        Success message when controller is saved
        
    Raises:
        HTTPException: 400 if controller type mismatch or save error
    """
    # If type is provided in body, validate it matches URL
    if controller.type is not None and controller.type != controller_type:
        raise HTTPException(
            status_code=400, 
            detail=f"Controller type mismatch: URL has '{controller_type}', body has '{controller.type}'"
        )
    
    try:
        fs_util.add_file(
            f'controllers/{controller_type.value}', 
            f"{controller_name}.py", 
            controller.content, 
            override=True
        )
        return {"message": f"Controller '{controller_name}' saved successfully in '{controller_type.value}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{controller_type}/{controller_name}")
async def delete_controller(controller_type: ControllerType, controller_name: str):
    """
    Delete a controller.
    
    Args:
        controller_type: Type of the controller
        controller_name: Name of the controller to delete
        
    Returns:
        Success message when controller is deleted
        
    Raises:
        HTTPException: 404 if controller not found
    """
    try:
        fs_util.delete_file(f'controllers/{controller_type.value}', f"{controller_name}.py")
        return {"message": f"Controller '{controller_name}' deleted successfully from '{controller_type.value}'"}
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, 
            detail=f"Controller '{controller_name}' not found in '{controller_type.value}'"
        )


@router.get("/{controller_type}/{controller_name}/config/template")
async def get_controller_config_template(controller_type: ControllerType, controller_name: str):
    """
    Get controller configuration template with default values.
    
    Args:
        controller_type: Type of the controller
        controller_name: Name of the controller
        
    Returns:
        Dictionary with configuration template and default values
        
    Raises:
        HTTPException: 404 if controller configuration class not found
    """
    config_class = fs_util.load_controller_config_class(controller_type.value, controller_name)
    if config_class is None:
        raise HTTPException(
            status_code=404, 
            detail=f"Controller configuration class for '{controller_name}' not found"
        )

    # Extract fields and default values
    config_fields = {name: {"default": field.default,
                            "type": field.annotation,
                            "required": field.required if hasattr(field, 'required') else False,
                            } for name, field in config_class.model_fields.items()}
    return json.loads(json.dumps(config_fields, default=str))

@router.post("/{controller_type}/{controller_name}/config/validate")
async def validate_controller_config(controller_type: ControllerType, controller_name: str, config: Dict):
    """
    Validate controller configuration against the controller's config class.

    Args:
        controller_type: Type of the controller
        controller_name: Name of the controller
        config: Configuration dictionary to validate

    Returns:
        Success message if configuration is valid

    Raises:
        HTTPException: 400 if validation fails
    """
    config_class = fs_util.load_controller_config_class(controller_type.value, controller_name)
    if config_class is None:
        raise HTTPException(
            status_code=404,
            detail=f"Controller configuration class for '{controller_name}' not found"
        )

    try:
        config_class(**config)  # Validate by instantiating the model
        return {"message": "Configuration is valid"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Bot-specific controller config endpoints
@router.get("/bots/{bot_name}/configs", response_model=List[Dict])
async def get_bot_controller_configs(bot_name: str):
    """
    Get all controller configurations for a specific bot.
    
    Args:
        bot_name: Name of the bot to get configurations for
        
    Returns:
        List of controller configurations for the bot
        
    Raises:
        HTTPException: 404 if bot not found
    """
    bots_config_path = f"instances/{bot_name}/conf/controllers"
    if not fs_util.path_exists(bots_config_path):
        raise HTTPException(status_code=404, detail=f"Bot '{bot_name}' not found")
    
    configs = []
    for controller_file in fs_util.list_files(bots_config_path):
        if controller_file.endswith('.yml'):
            config = fs_util.read_yaml_file(f"{bots_config_path}/{controller_file}")
            config['_config_name'] = controller_file.replace('.yml', '')
            configs.append(config)
    return configs


@router.post("/bots/{bot_name}/{controller_name}/config")
async def update_bot_controller_config(bot_name: str, controller_name: str, config: Dict):
    """
    Update controller configuration for a specific bot.
    
    Args:
        bot_name: Name of the bot
        controller_name: Name of the controller to update
        config: Configuration dictionary to update with
        
    Returns:
        Success message when configuration is updated
        
    Raises:
        HTTPException: 404 if bot or controller not found, 400 if update error
    """
    bots_config_path = f"instances/{bot_name}/conf/controllers"
    if not fs_util.path_exists(bots_config_path):
        raise HTTPException(status_code=404, detail=f"Bot '{bot_name}' not found")
    
    try:
        current_config = fs_util.read_yaml_file(f"{bots_config_path}/{controller_name}.yml")
        current_config.update(config)
        fs_util.dump_dict_to_yaml(f"{bots_config_path}/{controller_name}.yml", current_config)
        return {"message": f"Controller configuration for bot '{bot_name}' updated successfully"}
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, 
            detail=f"Controller configuration '{controller_name}' not found for bot '{bot_name}'"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))