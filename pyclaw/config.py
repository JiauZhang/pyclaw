import os
import warnings
from functools import lru_cache
from pathlib import Path

from conippets import json
from pyclaw.home import pyclaw_home


def config_file() -> Path:
    return pyclaw_home() / "config.json"


def configured_context_window() -> int:
    return int(load().get("contextWindow") or 0)


def checkpoints_enabled() -> bool:
    return bool(load().get("checkpoints", True))


def debug_on() -> bool:
    """Verbose logging is off unless asked for: a conversation log is meant to
    be a record of decisions and failures, not of every streamed token."""
    return bool(os.environ.get("PYCLAW_DEBUG")) or bool(load().get("debug"))


@lru_cache(maxsize=1)
def load() -> dict:
    defaults = {
        "gateway": {
            "http": {"port": 12321, "host": "127.0.0.1"},
        },
        "model": "agnes/agnes-2.5-flash",
        "contextWindow": 200_000,
        "enabled_channels": ["wechat"],
        "greeting_text": "PyClaw 已上线，随时为您服务！",
        "permissions": {"defaultMode": "default"},
        "banner": {"style": "random", "from": "#0084E4", "to": "#F0CC00", "angle": 60.0, "seed": None},
        "welcome": {"seen": 0, "lastVersion": ""},
        "notifications": {"backend": "auto", "idleSeconds": 60},
        "pricing": {},
    }
    if config_file().exists():
        try:
            saved = json.read(config_file())
            config = _deep_merge(defaults, saved)
        except Exception:
            config = defaults
        if config.get("rate_limit"):
            warnings.warn(
                'config key "rate_limit" is obsolete: rpm now lives in the chatchat config, '
                'set it with "pyclaw config --provider <name> --rpm <n>"',
                UserWarning,
            )
        if isinstance(config.get("provider"), str) and config["provider"]:
            warnings.warn(
                'config key "provider" is obsolete: the model is now one "provider/model" '
                'string in the "model" key, and this config value is ignored',
                UserWarning,
            )
    else:
        config = defaults
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    merged = {}
    for k in base | override:
        if k in base and k in override and isinstance(base[k], dict) and isinstance(override[k], dict):
            merged[k] = _deep_merge(base[k], override[k])
        elif k in override:
            merged[k] = override[k]
        else:
            merged[k] = base[k]
    return merged


def save(config: dict) -> None:
    config_file().parent.mkdir(parents=True, exist_ok=True)
    try:
        json.write(config_file(), config)
    except OSError:
        pass


def reload() -> dict:
    load.cache_clear()
    return load()
