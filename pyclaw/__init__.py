from .version import __version__

import os
from pathlib import Path

_pyclaw_home = os.environ.get("PYCLAW_HOME", str(Path.home() / ".pyclaw"))
os.environ["CHATCHAT_SECRET_FILE"] = os.path.join(_pyclaw_home, "chatchat.json")
os.environ["IMCHAT_HOME"] = _pyclaw_home

from chatchat.client import __secret_file__

from .gateway import GatewayServer, GatewayConfig
from .config import load
from .agents import Agent
from .channels import IMChannelAdapter

__all__ = [
    "GatewayServer",
    "GatewayConfig",
    "load",
    "Agent",
    "IMChannelAdapter",
]
