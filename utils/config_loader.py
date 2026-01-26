#!/usr/bin/env python3
"""
F1arr Configuration Loader
===========================

Loads settings with priority:
1. Environment variables (for Docker purists)
2. settings.yml (for easy configuration)
3. Defaults

Usage:
    from utils.config_loader import config
    
    prowlarr_url = config.get_prowlarr_url()
    # or
    prowlarr_url = config.get("prowlarr", "url")
"""

import os
import yaml
from pathlib import Path
from typing import Any, Optional, Dict

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.yml"

_settings_cache: Optional[Dict] = None


def _load_yaml() -> Dict:
    """Load settings from YAML file."""
    global _settings_cache
    
    if _settings_cache is not None:
        return _settings_cache
    
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r') as f:
                _settings_cache = yaml.safe_load(f) or {}
                return _settings_cache
        except Exception as e:
            print(f"Warning: Could not load settings.yml: {e}")
    
    _settings_cache = {}
    return _settings_cache


def reload_settings():
    """Force reload of settings from disk."""
    global _settings_cache
    _settings_cache = None
    return _load_yaml()


def get(section: str, key: str, default: Any = None) -> Any:
    """
    Get a setting value.
    
    Priority: Environment variable > settings.yml > default
    
    Args:
        section: Section in YAML (e.g., 'prowlarr', 'plex')
        key: Key within section (e.g., 'url', 'api_key')
        default: Default value if not found
    
    Returns:
        Setting value
    """
    # Build env var name: PROWLARR_URL, PLEX_TOKEN, etc.
    env_name = f"{section.upper()}_{key.upper()}"
    
    # Special mappings for backwards compatibility
    env_mappings = {
        ("plex", "library_id"): "PLEX_LIBRARY_ID",
        ("plex", "library_name"): "PLEX_LIBRARY",
        ("library", "f1_path"): "F1_LIBRARY_PATH",
        ("library", "downloads_path"): "F1_DOWNLOADS_PATH",
        ("discord", "webhook_url"): "DISCORD_WEBHOOK_URL",
    }
    
    mapped_name = env_mappings.get((section, key), env_name)
    
    # Check environment first
    env_value = os.environ.get(mapped_name)
    if env_value:
        return env_value
    
    # Fall back to settings.yml
    settings = _load_yaml()
    section_data = settings.get(section, {})
    
    if isinstance(section_data, dict):
        value = section_data.get(key)
        if value is not None and value != "":
            return value
    
    return default


def get_int(section: str, key: str, default: int = 0) -> int:
    """Get setting as integer."""
    value = get(section, key)
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_bool(section: str, key: str, default: bool = False) -> bool:
    """Get setting as boolean."""
    value = get(section, key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return default


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def get_prowlarr_url() -> Optional[str]:
    return get("prowlarr", "url")


def get_prowlarr_api_key() -> Optional[str]:
    return get("prowlarr", "api_key")


def get_sabnzbd_url() -> Optional[str]:
    return get("sabnzbd", "url")


def get_sabnzbd_api_key() -> Optional[str]:
    return get("sabnzbd", "api_key")


def get_plex_url() -> Optional[str]:
    return get("plex", "url")


def get_plex_token() -> Optional[str]:
    return get("plex", "token")


def get_plex_library_id() -> Optional[str]:
    return get("plex", "library_id")


def get_plex_library_name() -> str:
    return get("plex", "library_name", "Motorsport - F1")


def get_f1_library_path() -> str:
    return get("library", "f1_path", "/media/F1")


def get_downloads_path() -> str:
    return get("library", "downloads_path", "/downloads/complete/f1")


def get_discord_webhook() -> Optional[str]:
    return get("discord", "webhook_url")


def get_timezone() -> str:
    return os.environ.get("TZ") or get("timezone", default="Europe/London")


# =============================================================================
# VALIDATION
# =============================================================================

def validate() -> Dict[str, Any]:
    """
    Validate that required settings are configured.
    
    Returns:
        Dict with 'valid' bool, 'missing' list, and 'configured' list
    """
    required = [
        ("prowlarr", "url", "Prowlarr URL"),
        ("prowlarr", "api_key", "Prowlarr API Key"),
        ("sabnzbd", "url", "SABnzbd URL"),
        ("sabnzbd", "api_key", "SABnzbd API Key"),
        ("plex", "url", "Plex URL"),
        ("plex", "token", "Plex Token"),
    ]
    
    optional = [
        ("plex", "library_id", "Plex Library ID"),
        ("plex", "library_name", "Plex Library Name"),
        ("library", "f1_path", "F1 Library Path"),
        ("library", "downloads_path", "Downloads Path"),
        ("discord", "webhook_url", "Discord Webhook"),
    ]
    
    missing = []
    configured = []
    
    for section, key, name in required:
        value = get(section, key)
        if value:
            configured.append(name)
        else:
            missing.append(name)
    
    for section, key, name in optional:
        value = get(section, key)
        if value:
            configured.append(name)
    
    return {
        "valid": len(missing) == 0,
        "missing": missing,
        "configured": configured,
        "settings_file": str(SETTINGS_FILE),
        "settings_file_exists": SETTINGS_FILE.exists(),
    }


def get_all_settings() -> Dict[str, Any]:
    """Get all current settings (for display/debugging)."""
    return {
        "prowlarr": {
            "url": get_prowlarr_url(),
            "api_key": "***" if get_prowlarr_api_key() else None,
        },
        "sabnzbd": {
            "url": get_sabnzbd_url(),
            "api_key": "***" if get_sabnzbd_api_key() else None,
        },
        "plex": {
            "url": get_plex_url(),
            "token": "***" if get_plex_token() else None,
            "library_id": get_plex_library_id(),
            "library_name": get_plex_library_name(),
        },
        "library": {
            "f1_path": get_f1_library_path(),
            "downloads_path": get_downloads_path(),
        },
        "discord": {
            "webhook_url": "***" if get_discord_webhook() else None,
        },
        "timezone": get_timezone(),
    }


# =============================================================================
# SETTINGS FILE MANAGEMENT
# =============================================================================

def create_template() -> bool:
    """Create settings.yml template if it doesn't exist."""
    if SETTINGS_FILE.exists():
        return False
    
    template = """# F1arr Configuration
# ====================
# Edit these values to match your setup.
# Environment variables take priority over these settings.

prowlarr:
  url: ""          # e.g., http://192.168.1.100:9696
  api_key: ""

sabnzbd:
  url: ""          # e.g., http://192.168.1.100:8080
  api_key: ""

plex:
  url: ""          # e.g., http://192.168.1.100:32400
  token: ""
  library_id: ""   # Plex library section ID (e.g., "2")
  library_name: "Motorsport - F1"

library:
  f1_path: "/media/F1"
  downloads_path: "/downloads/complete/f1"

discord:
  webhook_url: ""  # Optional: for notifications

timezone: "Europe/London"
"""
    
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            f.write(template)
        return True
    except Exception as e:
        print(f"Error creating settings template: {e}")
        return False


def save_settings(settings: Dict) -> bool:
    """Save settings to YAML file."""
    global _settings_cache
    
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
        _settings_cache = settings
        return True
    except Exception as e:
        print(f"Error saving settings: {e}")
        return False


# =============================================================================
# MODULE TEST
# =============================================================================

if __name__ == "__main__":
    print("=== F1arr Configuration ===")
    print(f"Settings file: {SETTINGS_FILE}")
    print(f"Exists: {SETTINGS_FILE.exists()}")
    print()
    
    validation = validate()
    if validation["valid"]:
        print("✅ All required settings configured")
    else:
        print("❌ Missing required settings:")
        for item in validation["missing"]:
            print(f"   - {item}")
    
    print()
    print("Configured:")
    for item in validation["configured"]:
        print(f"   ✓ {item}")
    
    print()
    print("Current values:")
    settings = get_all_settings()
    for section, values in settings.items():
        if isinstance(values, dict):
            print(f"  {section}:")
            for k, v in values.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {section}: {values}")
