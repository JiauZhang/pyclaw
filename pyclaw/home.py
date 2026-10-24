from __future__ import annotations

import os
from pathlib import Path


def pyclaw_home() -> Path:
    return Path(os.environ.get("PYCLAW_HOME", "~/.pyclaw")).expanduser()
