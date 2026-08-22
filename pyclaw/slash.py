HELP = '''Available commands:
/help or /?   Show this help
/agent        Switch this session to single-agent mode
/team         Switch this session to team (multi-agent) mode
/clear        Clear the current session conversation history and token stats
/status       Show the current session runtime info
/tools        List tools available in this session
/thinking     Show or toggle thinking mode (on|off)'''


def _usage(session) -> dict:
    try:
        u = session.entity.total_usage
    except AttributeError:
        return {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    return {
        'prompt_tokens': u.prompt_tokens,
        'completion_tokens': u.completion_tokens,
        'total_tokens': u.total_tokens,
    }


def _status(session, session_key: str) -> str:
    client = session.entity.client
    msg_count = len(client.messages) if client and client.messages else 0
    usg = _usage(session)
    return (
        f"Session: {session_key or session.name}\n"
        f"Mode: {session.mode}\n"
        f"Provider: {session.entity.config.provider}\n"
        f"Model: {session.entity.config.model}\n"
        f"Thinking: {'on' if session.thinking else 'off'}\n"
        f"Context messages: {msg_count}\n"
        f"Active sub-agents: {len(session.entity.sub_agents)}\n"
        f"Tokens: prompt={usg['prompt_tokens']}, completion={usg['completion_tokens']}, total={usg['total_tokens']}\n"
        f"Available tools: {len(session.available_tools)}"
    )


_TRUE = {'on', '1', 'true', 'yes'}
_FALSE = {'off', '0', 'false', 'no'}


def _handle_thinking(session, arg: str) -> str:
    if not arg:
        return f'Thinking: {"on" if session.thinking else "off"}'
    if arg.lower() in _TRUE:
        session.set_thinking(True)
        return 'Thinking: on'
    if arg.lower() in _FALSE:
        session.set_thinking(False)
        return 'Thinking: off'
    return f'Invalid value: {arg}. Use on|off or leave empty to show current.'


async def handle_slash(text: str, session, session_key: str = '') -> str | None:
    text = text.strip()
    if not text.startswith('/'):
        return None
    cmd, _, arg = text[1:].partition(' ')
    cmd = cmd.strip().lower()
    arg = arg.strip()

    if cmd in ('help', 'h', '?'):
        return HELP
    if cmd == 'agent':
        await session.switch('agent')
        return 'Switched to single-agent mode.'
    if cmd == 'team':
        await session.switch('team')
        return 'Switched to team (multi-agent) mode.'
    if cmd == 'clear':
        session.reset()
        return 'Conversation history and token stats cleared.'
    if cmd == 'status':
        return _status(session, session_key)
    if cmd == 'tools':
        return 'Available tools: ' + ', '.join(session.available_tools)
    if cmd == 'thinking':
        return _handle_thinking(session, arg)

    return (f'Unknown command: /{cmd}.\n\n{HELP}')