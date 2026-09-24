from __future__ import annotations

from pathlib import Path

from pyclaw.tui.formatting import _clip_lines, _display_path, _preview
from pyclaw.tui.theme import (DISPLAY_NAMES, MAX_COMMAND_CHARS,
                              MAX_COMMAND_LINES, MEMORY_FILE_NAME)


def is_memory_path(value) -> bool:
    return Path(str(value or '')).name == MEMORY_FILE_NAME


def _data(tool_input) -> dict:
    return tool_input if isinstance(tool_input, dict) else {}


class ToolUI:
    def label(self, name, tool_input) -> str:
        return DISPLAY_NAMES.get(name, name)

    def args(self, name, tool_input, cwd) -> str:
        if not isinstance(tool_input, dict):
            return _clip_lines(tool_input, MAX_COMMAND_LINES, MAX_COMMAND_CHARS)
        pairs = ", ".join(f"{k}: {v}" for k, v in tool_input.items())
        return _clip_lines(pairs, MAX_COMMAND_LINES, MAX_COMMAND_CHARS)

    def summary(self, name, output, width) -> str:
        return _preview(output if output is not None else "", width)

    def kinds(self, name, tool_input) -> set:
        return set()

    def key(self, name, tool_input) -> str:
        data = _data(tool_input)
        return str(data.get('file_path') or data.get('path') or name)

    def hidden(self, name, tool_input) -> bool:
        return False


def build_tool_ui(**overrides) -> ToolUI:
    ui = ToolUI()
    for key, value in overrides.items():
        setattr(ui, key, value)
    return ui


TOOL_UIS: dict[str, ToolUI] = {}


def register(name: str, ui: ToolUI) -> None:
    TOOL_UIS[name] = ui


_DEFAULT = ToolUI()


def _ui(name: str) -> ToolUI:
    return TOOL_UIS.get(name, _DEFAULT)


def _safe(method: str, name: str, *args):
    try:
        return getattr(_ui(name), method)(name, *args)
    except Exception:
        return getattr(_DEFAULT, method)(name, *args)


def tool_label(name: str, tool_input) -> str:
    return _safe('label', name, tool_input)


def tool_args(name: str, tool_input, cwd) -> str:
    return _safe('args', name, tool_input, cwd)


def result_summary(name: str, output, width: int) -> str:
    text = str(output if output is not None else "")
    if not text:
        return "Done"
    if text.startswith("Error"):
        return text.split("\n")[0]
    return _safe('summary', name, output, width)


def collapse_kinds(name: str, tool_input) -> set:
    return _safe('kinds', name, tool_input)


def read_key(name: str, tool_input) -> str:
    return _safe('key', name, tool_input)


def hidden_card(name: str, tool_input) -> bool:
    return _safe('hidden', name, tool_input)


def path_args(tool_input, cwd) -> str:
    data = _data(tool_input)
    return _display_path(cwd, data.get("file_path") or data.get("path"))


def search_args(tool_input, cwd) -> str:
    data = _data(tool_input)
    parts = [f'pattern: "{data.get("pattern", "")}"']
    target = data.get("path")
    if target:
        parts.append(f'path: "{_display_path(cwd, target)}"')
    return ", ".join(parts)
