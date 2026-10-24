"""The layering rules that keep a tool importable without the application.

`import pyclaw.tools` used to load the gateway, the session and textual, because
the tools carried their own transcript cards and the package loaded everything
on import. These two rules are what stops that from creeping back.
"""

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "pyclaw"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _files(*parts):
    return sorted(PACKAGE.glob("/".join(parts)))


def test_a_tool_never_imports_the_interface():
    offenders = {}
    for path in _files("tools", "*.py"):
        found = {name for name in _imports(path) if name == "textual" or name.startswith("pyclaw.tui")}
        if found:
            offenders[path.name] = sorted(found)
    assert offenders == {}


def test_only_the_interface_imports_textual():
    offenders = {}
    for path in _files("**", "*.py"):
        if path.parts[-2] == "tui" or "tui" in path.parts:
            continue
        found = {name for name in _imports(path) if name == "textual"}
        if found:
            offenders[str(path.relative_to(PACKAGE))] = sorted(found)
    assert offenders == {}


def test_the_session_does_not_import_the_builder():
    offenders = {}
    for path in _files("session", "*.py"):
        found = {name for name in _imports(path) if name.startswith("pyclaw.team.builder")}
        if found:
            offenders[path.name] = sorted(found)
    assert offenders == {}
