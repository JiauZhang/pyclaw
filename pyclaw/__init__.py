from .version import __version__

import os
from pathlib import Path

__pyclaw_home__ = os.environ.get("PYCLAW_HOME", str(Path.home() / ".pyclaw"))
os.environ["PYCLAW_HOME"] = __pyclaw_home__
os.environ["CHATCHAT_HOME"] = __pyclaw_home__
os.environ["IMCHAT_HOME"] = __pyclaw_home__

__secret_file__ = str(Path(__pyclaw_home__) / "chatchat.json")

from .gateway import GatewayServer, GatewayConfig
from .config import load
from .agents import Session, build_team, IM_EXTRA
from .channels import IMChannelAdapter

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
