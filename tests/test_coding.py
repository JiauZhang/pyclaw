import asyncio
import tempfile
from pathlib import Path

from pyclaw.tools.coding import (PermissionController, build_coding_tools,
                                 next_mode, parse_mode)


def _tools(d):
    return {t.name: t for t in build_coding_tools(d)}


def _read(p):
    return p.read_text(encoding="utf-8")


def test_read_and_find():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("alpha\nbeta\n")
        t = _tools(d)
        out = t["Read"](file_path="a.txt")
        assert "alpha" in out and "a.txt" in out
        g = t["Glob"](pattern="*.txt")
        assert "a.txt" in g
        ls = t["LS"](path=".")
        assert "a.txt" in ls


def test_grep_matches_and_limits():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("x1\n---\nx2\n")
        t = _tools(d)
        out = asyncio.run(t["Grep"](pattern="x[0-9]"))
        assert "a.txt:1: x1" in out and "a.txt:3: x2" in out


def test_path_escape_guard():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        for tool, kw in (("Read", {"file_path": "../secret"}),
                         ("Write", {"file_path": "../x", "content": "y"})):
            assert t[tool](**kw).startswith("Error")


def test_edit_unique_and_ambiguous():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("one\ntwo\none\n")
        t = _tools(d)
        ambiguous = t["Edit"](file_path="a.txt", old_string="one",
                              new_string="X")
        assert "not unique" in ambiguous
        ok = t["Edit"](file_path="a.txt", old_string="two", new_string="TWO")
        assert "Edited" in ok and "TWO" in _read(root / "a.txt")
        missing = t["Edit"](file_path="a.txt", old_string="zzz",
                            new_string="y")
        assert "not found" in missing


def test_write_and_multi_edit():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        t = _tools(d)
        assert "Wrote" in t["Write"](file_path="b.txt", content="hi\n")
        out = t["MultiEdit"](file_path="b.txt", edits=[
            {"old_string": "hi", "new_string": "hello"}])
        assert "Edited" in out and "hello" in _read(root / "b.txt")


def test_mode_parse_and_cycle():
    for m in ("default", "acceptEdits", "plan"):
        assert parse_mode(m).value == m
    assert next_mode("default").value == "acceptEdits"
    assert next_mode("acceptEdits").value == "plan"
    assert next_mode("plan").value == "default"


def test_decide_matrix():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d)
        assert g.decide("Read", {"file_path": "a.txt"}) == "allow"
        assert g.decide("Edit", {"file_path": "a.txt"}) == "ask"
        assert g.decide("Write", {"file_path": "../x"}) == "ask"
        assert g.decide("Read", {"file_path": "../x"}) == "ask"
        assert g.decide("datetime", {}) == "allow"

        plan = PermissionController(mode="plan", cwd=d)
        assert plan.decide("Edit", {"file_path": "a.txt"}) == "deny"
        assert plan.decide("Read", {"file_path": "../x"}) == "allow"

        ae = PermissionController(mode="acceptEdits", cwd=d)
        assert ae.decide("Edit", {"file_path": "a.txt"}) == "allow"


def test_deny_removes_tool_and_precedence():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, deny=["Edit"])
        assert g.allowed_tool("Edit") is False
        assert g.allowed_tool("Read") is True
        g2 = PermissionController(mode="default", cwd=d,
                                  deny=["Edit"], allow=["Edit"])
        assert g2.decide("Edit", {"file_path": "a.txt"}) == "deny"


def test_ask_flow_authorize():
    with tempfile.TemporaryDirectory() as d:

        async def approve(name, inp):
            return "approved"

        g = PermissionController(mode="default", cwd=d, request=approve)
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True

        async def denied(name, inp):
            return "denied"

        g2 = PermissionController(mode="default", cwd=d, request=denied)
        res = asyncio.run(g2.authorize("Edit", {"file_path": "a.txt"}))
        assert isinstance(res, dict) and res["decision"] == "block"

        g3 = PermissionController(mode="default", cwd=d)
        res3 = asyncio.run(g3.authorize("Edit", {"file_path": "a.txt"}))
        assert isinstance(res3, dict) and res3["decision"] == "block"


def test_dont_ask_persists_allow():
    with tempfile.TemporaryDirectory() as d:

        async def once(name, inp):
            return "dont_ask"

        g = PermissionController(mode="default", cwd=d, request=once)
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True
        assert "Edit" in g._allow
        assert g.decide("Edit", {"file_path": "a.txt"}) == "allow"