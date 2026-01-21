import json
from typing import Dict, List

import yaml
from fastapi import APIRouter, HTTPException
from starlette import status

from models import Script, ScriptConfig
from utils.file_system import fs_util

router = APIRouter(tags=["Scripts"], prefix="/scripts")


@router.get("", response_model=List[str])
async def list_scripts():
    """
    List all available scripts.
    
    Returns:
        List of script names (without .py extension)
    """
    return [f.replace('.py', '') for f in fs_util.list_files('scripts') if f.endswith('.py')]


# Script Configuration endpoints (must come before script name routes)
@router.get("/configs", response_model=List[Dict])
async def list_script_configs():
    """
    List all script configurations with metadata.
    
    Returns:
        List of script configuration objects with name, script_file_name, and other metadata
    """
    try:
        config_files = [f for f in fs_util.list_files('conf/scripts') if f.endswith('.yml')]
        configs = []
        
        for config_file in config_files:
            config_name = config_file.replace('.yml', '')
            try:
                config = fs_util.read_yaml_file(f"conf/scripts/{config_file}")
                configs.append({
                    "config_name": config_name,
                    "script_file_name": config.get("script_file_name", "unknown"),
                    "controllers_config": config.get("controllers_config", []),
                    "candles_config": config.get("candles_config", []),
                    "markets": config.get("markets", {})
                })
            except Exception as e:
                # If config is malformed, still include it with basic info
                configs.append({
                    "config_name": config_name,
                    "script_file_name": "error",
                    "error": str(e)
                })
        
        return configs
    except FileNotFoundError:
        return []


@router.get("/configs/{config_name}", response_model=Dict)
async def get_script_config(config_name: str):
    """
    Get script configuration by config name.
    
    Args:
        config_name: Name of the configuration file to retrieve
        
    Returns:
        Dictionary with script configuration
        
    Raises:
        HTTPException: 404 if configuration not found
    """
    try:
        config = fs_util.read_yaml_file(f"conf/scripts/{config_name}.yml")
        return config
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Configuration '{config_name}' not found")


@router.post("/configs/{config_name}", status_code=status.HTTP_201_CREATED)
async def create_or_update_script_config(config_name: str, config: Dict):
    """
    Create or update script configuration.
    
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
        fs_util.add_file('conf/scripts', f"{config_name}.yml", yaml_content, override=True)
        return {"message": f"Configuration '{config_name}' saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/configs/{config_name}")
async def delete_script_config(config_name: str):
    """
    Delete script configuration.
    
    Args:
        config_name: Name of the configuration file to delete
        
    Returns:
        Success message when configuration is deleted
        
    Raises:
        HTTPException: 404 if configuration not found
    """
    try:
        fs_util.delete_file('conf/scripts', f"{config_name}.yml")
        return {"message": f"Configuration '{config_name}' deleted successfully"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Configuration '{config_name}' not found")


@router.get("/{script_name}", response_model=Dict[str, str])
async def get_script(script_name: str):
    """
    Get script content by name.
    
    Args:
        script_name: Name of the script to retrieve
        
    Returns:
        Dictionary with script name and content
        
    Raises:
        HTTPException: 404 if script not found
    """
    try:
        content = fs_util.read_file(f"scripts/{script_name}.py")
        return {
            "name": script_name,
            "content": content
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Script '{script_name}' not found")


@router.post("/{script_name}", status_code=status.HTTP_201_CREATED)
async def create_or_update_script(script_name: str, script: Script):
    """
    Create or update a script.
    
    Args:
        script_name: Name of the script (from URL path)
        script: Script object with content
        
    Returns:
        Success message when script is saved
        
    Raises:
        HTTPException: 400 if save error occurs
    """
    try:
        fs_util.add_file('scripts', f"{script_name}.py", script.content, override=True)
        return {"message": f"Script '{script_name}' saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{script_name}")
async def delete_script(script_name: str):
    """
    Delete a script.
    
    Args:
        script_name: Name of the script to delete
        
    Returns:
        Success message when script is deleted
        
    Raises:
        HTTPException: 404 if script not found
    """
    try:
        fs_util.delete_file('scripts', f"{script_name}.py")
        return {"message": f"Script '{script_name}' deleted successfully"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Script '{script_name}' not found")


@router.get("/{script_name}/config/template", response_model=Dict)
async def get_script_config_template(script_name: str):
    """
    Get script configuration template with default values.
    
    Args:
        script_name: Name of the script to get template for
        
    Returns:
        Dictionary with configuration template and default values
        
    Raises:
        HTTPException: 404 if script configuration class not found
    """
    config_class = fs_util.load_script_config_class(script_name)
    if config_class is None:
        raise HTTPException(status_code=404, detail=f"Script configuration class for '{script_name}' not found")

    # Extract fields and default values
    config_fields = {name: field.default for name, field in config_class.model_fields.items()}
    return json.loads(json.dumps(config_fields, default=str))