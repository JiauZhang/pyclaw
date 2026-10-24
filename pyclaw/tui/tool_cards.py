"""How each built-in tool appears in the transcript, kept out of the tools.

A tool knows what it does; how the result is drawn is the interface's business.
Keeping the two apart is what lets a tool be imported without the interface:
before this file existed, importing pyclaw.tools pulled in textual.
"""

from __future__ import annotations

from pyclaw.permissions.bash_rules import base_command, split_commands
from pyclaw.tools.names import AGENT, BASH, EDIT, GLOB, GREP, READ, SEND_MESSAGE, WRITE
from pyclaw.tui.formatting import _clip_lines, _plural, _summarize
from pyclaw.tui.theme import MAX_COMMAND_CHARS, MAX_COMMAND_LINES, MAX_USE_ARG_CHARS
from pyclaw.tui.toolui import build_tool_ui, is_memory_path, path_args, register, search_args


def _agent_tool_name(tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    subagent = str(data.get("subagent_type") or "")
    if subagent and subagent != "general-purpose":
        return AGENT if subagent == "worker" else subagent
    return AGENT


def _spawn_label(name, tool_input):
    return _agent_tool_name(tool_input)


def _spawn_args(name, tool_input, cwd):
    data = tool_input if isinstance(tool_input, dict) else {}
    return _summarize(data.get("prompt") or data.get("name") or "", MAX_USE_ARG_CHARS)


def _spawn_summary(name, output, width):
    return "Done"


def _message_args(name, tool_input, cwd):
    data = tool_input if isinstance(tool_input, dict) else {}
    return _summarize(f"{data.get('to', '')}: {data.get('message', '')}", MAX_USE_ARG_CHARS)


def _message_hidden(name, tool_input):
    data = tool_input if isinstance(tool_input, dict) else {}
    return isinstance(data.get("message"), str)


def _input_path(tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    return data.get("file_path") or data.get("path") or ""


def _path_args(name, tool_input, cwd):
    return path_args(tool_input, cwd)


def _read_summary(name, output, width):
    rows = str(output if output is not None else "").split("\n")
    body = rows[1:] if rows and rows[0].endswith(":") else rows
    return f"Read {_plural(len([r for r in body if r.strip()]), 'line')}"


def _read_kinds(name, tool_input):
    return {"memory_read" if is_memory_path(_input_path(tool_input)) else "read"}


def _write_kinds(name, tool_input):
    return {"memory_write"} if is_memory_path(_input_path(tool_input)) else set()


BASH_SEARCH_COMMANDS = frozenset({"find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"})
BASH_READ_COMMANDS = frozenset(
    {"cat", "head", "tail", "less", "more", "wc", "stat", "file", "strings", "jq", "awk", "cut", "sort", "uniq", "tr"}
)
BASH_LIST_COMMANDS = frozenset({"ls", "tree", "du"})
BASH_NEUTRAL_COMMANDS = frozenset({"echo", "printf", "true", "false", ":"})


def _bash_kinds(name, tool_input):
    data = tool_input if isinstance(tool_input, dict) else {}
    command = data.get("command")
    try:
        parts = [p for p in split_commands(str(command or "")) if base_command(p) not in BASH_NEUTRAL_COMMANDS]
    except Exception:
        return set()
    if not parts:
        return set()
    kinds = set()
    for part in parts:
        base = base_command(part)
        if base in BASH_SEARCH_COMMANDS:
            kinds.add("search")
        elif base in BASH_READ_COMMANDS:
            kinds.add("read")
        elif base in BASH_LIST_COMMANDS:
            kinds.add("list")
        else:
            return {"bash"}
    # One command is one entry in the group: listing outranks searching, which
    # outranks reading, however many stages the pipeline has.
    for kind in ("list", "search", "read"):
        if kind in kinds:
            return {kind}
    return kinds


def _bash_args(name, tool_input, cwd):
    data = tool_input if isinstance(tool_input, dict) else {}
    return _clip_lines(data.get("command", ""), MAX_COMMAND_LINES, MAX_COMMAND_CHARS)


def _glob_summary(name, output, width):
    hits = [r for r in str(output if output is not None else "").split("\n") if r.strip()]
    return f"Found {_plural(len(hits), 'file')}"


def _grep_summary(name, output, width):
    hits = [r for r in str(output if output is not None else "").split("\n") if r.strip()]
    if hits and ":" in hits[0]:
        return f"Found {_plural(len(hits), 'line')}"
    return f"Found {_plural(len(hits), 'file')}"


def _search_args(name, tool_input, cwd):
    return search_args(tool_input, cwd)


def _search_kinds(name, tool_input):
    return {"search"}


register(AGENT, build_tool_ui(label=_spawn_label, args=_spawn_args, summary=_spawn_summary))
register(SEND_MESSAGE, build_tool_ui(args=_message_args, hidden=_message_hidden))
register(READ, build_tool_ui(args=_path_args, summary=_read_summary, kinds=_read_kinds))
register(WRITE, build_tool_ui(args=_path_args, kinds=_write_kinds))
register(EDIT, build_tool_ui(args=_path_args, kinds=_write_kinds))
register(BASH, build_tool_ui(args=_bash_args, kinds=_bash_kinds))
register(GLOB, build_tool_ui(args=_search_args, summary=_glob_summary, kinds=_search_kinds))
register(GREP, build_tool_ui(args=_search_args, summary=_grep_summary, kinds=_search_kinds))
