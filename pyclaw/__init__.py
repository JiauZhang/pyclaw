from .version import __version__

import os
from pathlib import Path

def pyclaw_home() -> Path:
    return Path(os.environ.get("PYCLAW_HOME", "~/.pyclaw")).expanduser()


__pyclaw_home__ = str(pyclaw_home())
os.environ["PYCLAW_HOME"] = __pyclaw_home__
os.environ["CHATCHAT_HOME"] = __pyclaw_home__
os.environ["IMCHAT_HOME"] = __pyclaw_home__

__secret_file__ = str(pyclaw_home() / "chatchat.json")

from .gateway import GatewayServer, GatewayConfig
from .config import load
from .agents import Session
from .team_builder import IM_EXTRA, build_team
from .channels import IMChannelAdapter

from chatchat.hooks.events import clear_runtime_sinks

clear_runtime_sinks()

__all__ = [
    "GatewayServer",
    "GatewayConfig",
    "load",
    "Session",
    "build_team",
    "IM_EXTRA",
    "IMChannelAdapter",
    "__pyclaw_home__",
    "__secret_file__",
]
