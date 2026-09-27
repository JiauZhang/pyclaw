from __future__ import annotations

import os
import pwd
from pathlib import Path


def _real_user_home() -> Path:
    """用户数据库里的真实主目录，不依赖可能缺失/异常的 $HOME。"""
    return Path(pwd.getpwuid(os.getuid()).pw_dir or os.path.sep)


def pyclaw_home() -> Path:
    path = Path(os.environ.get("PYCLAW_HOME", "~/.pyclaw")).expanduser()
    # launchd 拉起的 GUI App 里 $HOME 可能缺失/为空/指向根目录，
    # "~/.pyclaw" 会被展开到只读的 /.pyclaw——回落到真实主目录。
    if path.parent == Path(os.path.sep) and path.name == ".pyclaw":
        path = _real_user_home() / ".pyclaw"
    return path


__pyclaw_home__ = str(pyclaw_home())

__secret_file__ = str(pyclaw_home() / "chatchat.json")

os.environ["PYCLAW_HOME"] = __pyclaw_home__
os.environ["CHATCHAT_HOME"] = __pyclaw_home__
os.environ["IMCHAT_HOME"] = __pyclaw_home__
