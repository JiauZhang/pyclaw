from functools import lru_cache
from pathlib import Path

from conippets import json
from pyclaw import __pyclaw_home__

__config_file__ = Path(__pyclaw_home__) / "config.json"


@lru_cache(maxsize=1)
def load() -> dict:
    config = {
        "gateway": {
            "http": {"port": 12321, "host": "127.0.0.1"},
        },
        "provider": "openrouter",
        "model": "tencent/hy3-preview:free",
        "enabled_channels": ["web"],
        "greeting_text": "PyClaw 已上线，随时为您服务！",
    }
    if __config_file__.exists():
        try:
            config |= json.read(__config_file__)
        except Exception:
            pass
    return config


def save(config: dict) -> None:
    __config_file__.parent.mkdir(parents=True, exist_ok=True)
    json.write(__config_file__, config)


def reload() -> dict:
    load.cache_clear()
    return load()
