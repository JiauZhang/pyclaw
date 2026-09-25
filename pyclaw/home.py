from __future__ import annotations

import os
from pathlib import Path


def pyclaw_home() -> Path:
    return Path(os.environ.get("PYCLAW_HOME", "~/.pyclaw")).expanduser()


__pyclaw_home__ = str(pyclaw_home())

__secret_file__ = str(pyclaw_home() / "chatchat.json")

os.environ["PYCLAW_HOME"] = __pyclaw_home__
os.environ["CHATCHAT_HOME"] = __pyclaw_home__
os.environ["IMCHAT_HOME"] = __pyclaw_home__
