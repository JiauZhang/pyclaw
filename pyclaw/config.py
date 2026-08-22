from functools import lru_cache
from pathlib import Path

from conippets import json
from pyclaw import __pyclaw_home__

__config_file__ = Path(__pyclaw_home__) / "config.json"


@lru_cache(maxsize=1)
def load() -> dict:
    defaults = {
        "gateway": {
            "http": {"port": 12321, "host": "127.0.0.1"},
        },
        "provider": "agnes",
        "model": "agnes-2.5-flash",
        "enabled_channels": ["wechat"],
        "greeting_text": "PyClaw 已上线，随时为您服务！",
    }
    if __config_file__.exists():
        try:
            saved = json.read(__config_file__)
            config = _deep_merge(defaults, saved)
        except Exception:
            config = defaults
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
    __config_file__.parent.mkdir(parents=True, exist_ok=True)
    try:
        json.write(__config_file__, config)
    except OSError:
        pass


def reload() -> dict:
    load.cache_clear()
    return load()
