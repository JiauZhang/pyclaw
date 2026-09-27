import os
import pwd
from pathlib import Path

from pyclaw.home import pyclaw_home


def _real_home() -> Path:
    """用户数据库里的真实主目录，不依赖可能异常的 $HOME。"""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_an_empty_home_falls_back_to_the_real_user_home(monkeypatch):
    """GUI App（launchd 拉起）的 HOME 可能缺失或为空，此时
    "~/.pyclaw" 会被展开到只读根目录 /.pyclaw——必须回落到真实主目录。"""
    monkeypatch.setenv("HOME", "")
    monkeypatch.delenv("PYCLAW_HOME", raising=False)
    assert pyclaw_home() == _real_home() / ".pyclaw"


def test_a_root_home_falls_back_to_the_real_user_home(monkeypatch):
    monkeypatch.setenv("HOME", "/")
    monkeypatch.delenv("PYCLAW_HOME", raising=False)
    assert pyclaw_home() == _real_home() / ".pyclaw"


def test_an_explicit_pyclaw_home_is_respected(monkeypatch):
    monkeypatch.setenv("PYCLAW_HOME", "/tmp/keepme")
    assert pyclaw_home() == Path("/tmp/keepme")
