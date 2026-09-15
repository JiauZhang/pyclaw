import asyncio

import pytest

from pyclaw import slash


HELP_KEYWORDS = ("/help", "/clear", "/status", "/model", "/cost")


class _Usage:
    def __init__(self, prompt=0, completion=0, total=0, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        self.prompt_tokens_details = {"cached_tokens": cached} if cached else None


def _fake_session(**kwargs):
    class FakeSession:
        name = "s1"
        mode = "agent"
        thinking = False
        provider = "p"
        model = "m"
        available_tools = ["a", "b"]
        context_messages = 0
        active_agents = 0
        usage = _Usage()

        def __init__(self, **kw):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def reset(self):
            self._reset = True

        def set_model(self, model):
            self.model = model

    return FakeSession(**kwargs)


async def _call(*args, **kw):
    return await slash.handle_slash(*args, **kw)


def test_help_lists_commands():
    out = asyncio.run(_call("/help", _fake_session()))
    assert out is not None
    for kw in HELP_KEYWORDS:
        assert kw in out


def test_unknown_command_returns_help():
    out = asyncio.run(_call("/nope", _fake_session()))
    assert out.startswith("Unknown command")
    assert "/help" in out


def test_non_slash_returns_none():
    assert asyncio.run(_call("hello world", _fake_session())) is None


def test_clear_command():
    out = asyncio.run(_call("/clear", _fake_session()))
    assert "cleared" in out.lower()


def test_tools_and_thinking_commands_removed():
    assert "/tools" not in slash.HELP and "/thinking" not in slash.HELP
    assert "Unknown command" in asyncio.run(_call("/tools", _fake_session()))
    assert "Unknown command" in asyncio.run(_call("/thinking", _fake_session()))


def test_status_command():
    session = _fake_session()
    out = asyncio.run(_call("/status", session, session_key="k1"))
    assert "k1" in out
    assert "agent" in out


def test_status_includes_usage_and_cost():
    session = _fake_session(usage=_Usage(1200, 300, 1500))
    out = asyncio.run(_call("/status", session, session_key="k1"))
    assert "1200 in" in out
    assert "300 out" in out
    assert "unpriced" in out


def test_model_command_reports_current_model():
    out = asyncio.run(_call("/model", _fake_session()))
    assert out == "Model: m"


def test_model_command_switches_session_and_config(monkeypatch):
    from pyclaw import config as config_module
    saved = {}
    monkeypatch.setattr(config_module, "save", lambda c: saved.update(c))
    monkeypatch.setattr("pyclaw.load", lambda: {"model": "m"})

    session = _fake_session()
    out = asyncio.run(_call("/model newmodel", session))
    assert out == "Model: newmodel"
    assert session.model == "newmodel"
    assert saved["model"] == "newmodel"


def test_cost_command_unpriced(monkeypatch):
    from pyclaw import config as config_module
    monkeypatch.setattr(config_module, "load", lambda: {"pricing": {}})
    session = _fake_session(usage=_Usage(1200, 300, 1500))
    out = asyncio.run(_call("/cost", session))
    assert "unpriced" in out
    assert "pricing.m" in out
    assert "1200 in" in out


def test_cost_command_with_pricing(monkeypatch):
    from pyclaw import config as config_module
    monkeypatch.setattr(config_module, "load", lambda: {
        "pricing": {"m": {"input": 1, "output": 2}}})
    session = _fake_session(usage=_Usage(1000, 500, 1500))
    out = asyncio.run(_call("/cost", session))
    assert "$0.0020" in out


def test_mode_switch_commands_removed():
    """对齐 claude：agent/team 模式只能由启动 flag 决定，slash 切换已移除。"""
    out = asyncio.run(_call("/agent", _fake_session()))
    assert "Unknown command" in out
    out = asyncio.run(_call("/team", _fake_session()))
    assert "Unknown command" in out
    assert "/agent" not in slash.HELP and "/team" not in slash.HELP


def test_compact_calls_session_compactor():
    class _S:
        compacted = False

        async def compact(self):
            self.compacted = True
            return 'Compacted: 10 -> 4 messages'

    s = _S()
    out = asyncio.run(_call("/compact", s))
    assert s.compacted is True
    assert "Compacted" in out


def test_permissions_lists_mode_and_rules_with_sources():
    class _S:
        permission_mode = "default"

        def permission_rules(self):
            return [("allow", "Bash(git commit:*)", "local"),
                    ("deny", "Bash(curl:*)", "user")]

    out = asyncio.run(_call("/permissions", _S()))
    assert "default" in out
    assert "allow" in out and "Bash(git commit:*)" in out and "local" in out
    assert "deny" in out and "Bash(curl:*)" in out and "user" in out


def test_permissions_remove_rule():
    class _S:
        removed = None

        def permission_mode(self):
            return "default"

        permission_mode = "default"

        def permission_rules(self):
            return [("allow", "Bash(a:*)", "local")]

        def remove_rule(self, rule):
            self.removed = rule
            return rule == "Bash(a:*)"

    s = _S()
    out = asyncio.run(_call("/permissions remove Bash(a:*)", s))
    assert s.removed == "Bash(a:*)"
    assert "Removed" in out
    out = asyncio.run(_call("/permissions remove Nope", _S()))
    assert "not found" in out


def test_suggest_prefix_hits_first():
    names = [c["name"] for c in slash.suggest("/he")]
    assert names[0] == "help"


def test_suggest_matches_alias_and_description():
    names = [c["name"] for c in slash.suggest("/h")]
    assert "help" in names                       # 别名 h 前缀命中
    names = [c["name"] for c in slash.suggest("/tok")]
    assert "cost" in names                       # 描述子串命中


def test_suggest_empty_query_lists_all_and_args_hide_menu():
    assert len(slash.suggest("/")) == len(slash.COMMANDS)
    assert slash.suggest("/model x") == []       # 已输入实参 → 隐藏
    assert slash.suggest("/model ") == []
    assert slash.suggest("hello") == []          # 非 / 开头


def test_suggest_case_insensitive():
    names = [c["name"] for c in slash.suggest("/MO")]
    assert names[0] == "model"
