# Legacy imports for backwards compatibility
from .env_loader import load_env, get_env, get_env_int, get_env_bool

# New config loader
from . import config_loader as config
