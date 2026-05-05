from .version import __version__

import os
from pathlib import Path
os.environ['CHATCHAT_SECRET_FILE'] = str(Path.home() / ".pyclaw" / "chatchat.json")
from chatchat.client import __secret_file__

from .gateway import GatewayServer, GatewayConfig
from .config import load
from .agents import Agent

__all__ = [
    "GatewayServer",
    "GatewayConfig",
    "load",
    "Agent",
]
