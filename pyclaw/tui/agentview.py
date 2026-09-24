from __future__ import annotations

from rich.markup import escape

from pyclaw.tui.formatting import _summarize, _teammate_blocks, _user_markup
from pyclaw.tui.theme import (BULLET, BULLET_PREFIX, POINTER, PREVIEW_CHARS,
                              PREVIEW_LINES, RESULT_PREFIX,
                              TEAMMATE_VIEW_HINT)
from pyclaw.tui.toolui import tool_args, tool_label


def agent_prompt(agent) -> str:
    for message in getattr(agent, 'messages', []) or []:
        if not isinstance(message, dict):
            continue
        if message.get('role') == 'user' \
                and isinstance(message.get('content'), str):
            return message['content']
    return str(getattr(agent, 'instruction', '') or '')


def tool_line(block: dict, cwd: str) -> str:
    name = str(block.get('name', 'tool'))
    tool_input = block.get('input', '')
    args = tool_args(name, tool_input, cwd)
    label = escape(tool_label(name, tool_input))
    return f"{BULLET} [bold]{label}[/]({escape(args)})"


def agent_entries(agent, *, cwd: str, color_for) -> list:
    entries = []
    for message in getattr(agent, 'messages', []) or []:
        if not isinstance(message, dict):
            continue
        role = message.get('role')
        content = message.get('content')
        if role == 'user':
            entries += _user_entries(content, cwd=cwd, color_for=color_for)
            continue
        if role != 'assistant':
            continue
        if isinstance(content, str):
            text = content.strip('\n')
            if text:
                entries.append(f"{BULLET_PREFIX}{escape(text)}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) \
                        and block.get('type') == 'tool_use':
                    entries.append(tool_line(block, cwd))
    return entries


def _user_entries(content, *, cwd: str, color_for) -> list:
    if isinstance(content, str):
        blocks = _teammate_blocks(content)
        if blocks is None:
            return [_user_markup(content, color_for)] if content.strip() else []
        return [f"[{color_for(str(sender))}]@{escape(str(sender))}[/]"
                f"{POINTER} {escape(body)}" for sender, body in blocks]
    if not isinstance(content, list):
        return []
    entries = []
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'tool_result':
            continue
        body = str(block.get('content', '')).strip()
        head = body.splitlines()[0] if body else ''
        entries.append(f"[dim]{RESULT_PREFIX}{escape(_summarize(head, 100))}[/]")
    return entries


def agent_preview(agent, *, cwd: str) -> list[str]:
    if agent is None:
        return []
    lines: list[str] = []
    for message in reversed(getattr(agent, 'messages', []) or []):
        if len(lines) >= PREVIEW_LINES or not isinstance(message, dict):
            break
        content = message.get('content')
        parts = [content] if isinstance(content, str) else (
            content if isinstance(content, list) else [])
        for block in reversed(parts):
            if len(lines) >= PREVIEW_LINES:
                break
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                name = str(block.get('name', 'tool'))
                args = tool_args(name, block.get('input'), cwd)
                lines.append(_summarize(args or f"Using {name}",
                                        PREVIEW_CHARS))
            elif block.get('type') == 'text':
                text = [line for line in str(block.get('text', '')).split('\n')
                        if line.strip()]
                for line in reversed(text):
                    if len(lines) >= PREVIEW_LINES:
                        break
                    lines.append(_summarize(line.strip(), PREVIEW_CHARS))
    lines.reverse()
    return [_summarize(line, PREVIEW_CHARS) for line in lines]


def agent_view_markup(agent, *, cwd: str, color_for) -> str:
    if agent is None:
        return "[dim]this agent is gone[/]"
    name = str(getattr(agent, 'name', ''))
    color = color_for(name)
    lines = [f"[bold]Viewing [/][{color}][bold]@{escape(name)}[/][/]"
             f"[dim] \u00b7 {TEAMMATE_VIEW_HINT}[/]"]
    prompt = agent_prompt(agent)
    if prompt:
        lines.append(f"[dim]{escape(_summarize(prompt, 200))}[/]")
    lines.append('')
    lines += agent_entries(agent, cwd=cwd, color_for=color_for)
    return '\n'.join(lines)
