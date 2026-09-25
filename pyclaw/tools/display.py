from __future__ import annotations

from pyclaw.tui.formatting import _summarize
from pyclaw.tui.theme import MAX_USE_ARG_CHARS
from pyclaw.tui.toolui import build_tool_ui, register
from .names import AGENT, SEND_MESSAGE


def _agent_tool_name(tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    subagent = str(data.get('subagent_type') or '')
    if subagent and subagent != 'general-purpose':
        return AGENT if subagent == 'worker' else subagent
    return AGENT


def _spawn_label(name, tool_input):
    return _agent_tool_name(tool_input)


def _spawn_args(name, tool_input, cwd):
    data = tool_input if isinstance(tool_input, dict) else {}
    return _summarize(data.get('prompt') or data.get('name') or '',
                      MAX_USE_ARG_CHARS)


def _spawn_summary(name, output, width):
    return 'Done'


def _message_args(name, tool_input, cwd):
    data = tool_input if isinstance(tool_input, dict) else {}
    return _summarize(f'{data.get("to", "")}: {data.get("message", "")}',
                      MAX_USE_ARG_CHARS)


def _message_hidden(name, tool_input):
    data = tool_input if isinstance(tool_input, dict) else {}
    return isinstance(data.get('message'), str)


register(AGENT, build_tool_ui(label=_spawn_label, args=_spawn_args,
                                       summary=_spawn_summary))
register(SEND_MESSAGE, build_tool_ui(args=_message_args,
                                       hidden=_message_hidden))
