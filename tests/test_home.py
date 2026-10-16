import os
from pathlib import Path

from pyclaw.home import pyclaw_home


def test_an_explicit_pyclaw_home_is_respected(monkeypatch):
    monkeypatch.setenv("PYCLAW_HOME", "/tmp/keepme")
    assert pyclaw_home() == Path("/tmp/keepme")


def test_the_default_expands_to_the_home_directory(monkeypatch):
    monkeypatch.delenv("PYCLAW_HOME", raising=False)
    assert pyclaw_home() == Path("~/.pyclaw").expanduser()
