from .version import __version__

import os
from pathlib import Path

__pyclaw_home__ = os.environ.get("PYCLAW_HOME", str(Path.home() / ".pyclaw"))
os.environ["PYCLAW_HOME"] = __pyclaw_home__
os.environ["CHATCHAT_SECRET_FILE"] = os.path.join(__pyclaw_home__, "chatchat.json")
os.environ["IMCHAT_HOME"] = __pyclaw_home__

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
    "__pyclaw_home__",
]
