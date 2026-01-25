"""
Environment variable loader with .env file support.
For containerized use, environment variables come from Docker.
"""

import os
from typing import Optional

_loaded = False

def load_env(env_file: str = None):
    """Load environment variables. In container, uses Docker env vars."""
    global _loaded
    if _loaded:
        return
    
    # Try to load python-dotenv if available
    try:
        from dotenv import load_dotenv
        
        # In container, check for mounted .env
        if env_file and os.path.exists(env_file):
            load_dotenv(env_file)
        elif os.path.exists("/app/.env"):
            load_dotenv("/app/.env")
        elif os.path.exists(".env"):
            load_dotenv(".env")
    except ImportError:
        pass  # dotenv not available, use environment directly
    
    _loaded = True

def get_env(key: str, default: str = None, required: bool = False) -> Optional[str]:
    """Get environment variable."""
    value = os.environ.get(key, default)
    if required and not value:
        raise ValueError(f"Required environment variable {key} is not set")
    return value

def get_env_int(key: str, default: int = 0) -> int:
    """Get environment variable as integer."""
    value = os.environ.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default

def get_env_bool(key: str, default: bool = False) -> bool:
    """Get environment variable as boolean."""
    value = os.environ.get(key, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default
