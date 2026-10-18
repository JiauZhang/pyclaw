import types

from pyclaw.team import defs as agent_defs
import pyclaw.plugins as plugins
from types import SimpleNamespace

AGENT_MD = """---
name: fixture-writer
description: "HF 论文日报专家，负责增量扫描与评分产出决策简报。"
tools: Bash
---

你是论文日报专家，负责增量扫描与评分产出决策简报。
"""


def _fake_entry_point(name, module):
    ep = types.SimpleNamespace()
    ep.name = name
    ep.load = lambda: module
    return ep


def _make_package_module(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "fixture-writer.md").write_text(AGENT_MD, encoding="utf-8")
    module = types.ModuleType("fixture_chatool_agents")
    module.AGENTS_DIR = agents_dir
    return module, agents_dir


def _patch_entry_points(monkeypatch, module):

    eps = [_fake_entry_point("fixture", module)]
    monkeypatch.setattr(plugins, "entry_points", lambda group=None: eps)
    return plugins


def test_discover_agent_dirs_collects_package_agents_dir(tmp_path, monkeypatch):

    module, agents_dir = _make_package_module(tmp_path)
    monkeypatch.setattr(plugins, "entry_points", lambda group=None: [_fake_entry_point("x", module)])
    dirs = plugins.discover_agent_dirs()
    assert (agents_dir, "x") in dirs


def test_discover_agent_dirs_ignores_packages_without_the_contract(tmp_path, monkeypatch):

    module = types.ModuleType("plain_package")
    monkeypatch.setattr(plugins, "entry_points", lambda group=None: [_fake_entry_point("p", module)])
    assert plugins.discover_agent_dirs() == []


def test_unified_loader_loads_md_recursively(tmp_path, monkeypatch):
    module, agents_dir = _make_package_module(tmp_path)
    nested = agents_dir / "nested"
    nested.mkdir()
    (nested / "extra-agent.md").write_text(
        '---\nname: extra-agent\ndescription: "d"\ntools: Bash\n---\n\nbody body body.', encoding="utf-8"
    )
    _patch_entry_points(monkeypatch, module)

    entries = agent_defs.discover("/tmp", [], package_dirs=[(agents_dir, "fixture")])
    by_type = {e.agent_type: e for e in entries}
    assert "fixture-writer" in by_type
    assert "extra-agent" in by_type
    for name in ("fixture-writer", "extra-agent"):
        assert by_type[name].scope == agent_defs.PACKAGE
    assert "你是论文日报专家" in by_type["fixture-writer"].defn.system_prompt


def test_tools_string_resolves_against_the_registry(tmp_path, monkeypatch):
    module, agents_dir = _make_package_module(tmp_path)
    _patch_entry_points(monkeypatch, module)

    tool = SimpleNamespace(name="Bash")
    entries = agent_defs.discover("/tmp", [tool], package_dirs=[(agents_dir, "fixture")])
    defn = {e.agent_type: e.defn for e in entries}["fixture-writer"]
    assert [t.name for t in defn.tools] == ["Bash"]


def test_md_without_frontmatter_name_is_skipped(tmp_path, monkeypatch):
    module, agents_dir = _make_package_module(tmp_path)
    _patch_entry_points(monkeypatch, module)
    (agents_dir / "README.md").write_text("just a readme\n", encoding="utf-8")

    entries = agent_defs.discover("/tmp", [], package_dirs=[(agents_dir, "fixture")])
    assert "fixture-writer" in {e.agent_type for e in entries}
    assert "README" not in {e.agent_type for e in entries}
