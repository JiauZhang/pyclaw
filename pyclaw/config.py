"""Configuration loader."""

from pathlib import Path
from typing import Optional

from conippets import json

DEFAULT_PATH = Path.home() / ".pyclaw" / "config.json"

_cache: Optional[dict] = None


def _defaults():
    return {
        "version": "1.0",
        "gateway": {
            "http": {"enabled": True, "port": 12321, "host": "127.0.0.1", "cors_origins": []},
            "websocket": {"enabled": True, "ping_interval": 30, "ping_timeout": 10},
            "control_ui": {"enabled": True},
            "auth": {},
        },
        "models": {},
        "default_model": None,
        "channels": {},
        "agents": {
            "default": {
                "name": "Default Agent",
                "description": "Default PyClaw agent",
                "model": None,
                "system_prompt": "You are a helpful AI assistant.",
                "tools": ["echo", "time"],
                "memory": True,
                "max_iterations": 10,
            }
        },
        "default_agent": "default",
        "tools": {
            "exec": {"enabled": True, "ask": True, "timeout": 60},
            "browser": {"enabled": False},
        },
        "sessions": {"store_path": "~/.pyclaw/sessions", "max_history": 100, "ttl_hours": None},
        "skills": {"enabled": True, "auto_enable": False, "paths": []},
        "logging": {"level": "INFO", "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"},
    }


def load(path: Optional[Path] = None) -> dict:
    global _cache
    if _cache is not None:
        return _cache

    config_path = path or DEFAULT_PATH
    config = _defaults()

    if config_path.exists():
        try:
            file_config = json.read(config_path)
            if isinstance(file_config, dict):
                config.update(file_config)
        except Exception:
            pass

    _cache = config
    return _cache


def reload(path: Optional[Path] = None) -> dict:
    global _cache
    _cache = None
    return load(path)
