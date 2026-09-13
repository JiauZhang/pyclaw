HELP = '''Available commands:
/help or /?   Show this help
/agent        Switch this session to single-agent mode
/team         Switch this session to team (multi-agent) mode
/clear        Clear the current session conversation history and token stats
/status       Show the current session runtime info
/tools        List tools available in this session
/thinking     Show or toggle thinking mode (on|off)
/permissions  Show/switch permission mode, list saved permission rules
/plan         Enter plan (read-only) mode
/model        Show or switch the model for this session
/cost         Show token usage and estimated cost'''


def _pricing() -> dict:
    from pyclaw.config import load as load_config
    return load_config().get('pricing') or {}


def _cost_of(session):
    from pyclaw.cost import usage_cost
    return usage_cost(session.model, session.usage, _pricing())


def _status(session, session_key: str) -> str:
    from pyclaw.cost import format_cost
    usage = session.usage
    return (
        f"Session: {session_key or session.name}\n"
        f"Mode: {session.mode}\n"
        f"Provider: {session.provider}\n"
        f"Model: {session.model}\n"
        f"Thinking: {'on' if session.thinking else 'off'}\n"
        f"Context messages: {session.context_messages}\n"
        f"Active sub-agents: {session.active_agents}\n"
        f"Available tools: {len(session.available_tools)}\n"
        f"Usage: {usage.prompt_tokens} in / {usage.completion_tokens} out / "
        f"{usage.total_tokens} total\n"
        f"Cost: {format_cost(_cost_of(session))}"
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


def _handle_permissions(session, arg: str) -> str:
    if arg.startswith('remove '):
        rule = arg[len('remove '):].strip()
        if not rule:
            return 'Usage: /permissions remove <rule>'
        remover = getattr(session, 'remove_rule', None)
        if remover is None or not remover(rule):
            return f'Rule not found or read-only: {rule}'
        return f'Removed rule: {rule}'
    if arg:
        try:
            session.set_permission_mode(arg)
        except ValueError as e:
            return str(e)
    lines = [f'Permission mode: {session.permission_mode}']
    rules = (session.permission_rules()
             if hasattr(session, 'permission_rules') else [])
    if rules:
        lines.append('Rules:')
        for behavior, rule, source in rules:
            lines.append(f'  [{behavior}] {rule}  ({source})')
    return '\n'.join(lines)


def _handle_model(session, arg: str) -> str:
    if not arg:
        return f'Model: {session.model}'
    session.set_model(arg)
    from pyclaw import load as load_config
    from pyclaw.config import save as save_config
    config = load_config()
    config['model'] = session.model
    save_config(config)
    return f'Model: {session.model}'


def _handle_cost(session, arg: str) -> str:
    from pyclaw.cost import format_cost
    usage = session.usage
    cost = _cost_of(session)
    detail = format_cost(cost)
    if cost is None:
        detail += f' (add pricing.{session.model} to config)'
    return (f"Model: {session.model}\n"
            f"Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out"
            f" / {usage.total_tokens} total\n"
            f"Cost: {detail}")


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
        return 'Conversation history cleared.'
    if cmd == 'status':
        return _status(session, session_key)
    if cmd == 'tools':
        return 'Available tools: ' + ', '.join(session.available_tools)
    if cmd == 'thinking':
        return _handle_thinking(session, arg)
    if cmd == 'permissions':
        return _handle_permissions(session, arg)
    if cmd == 'plan':
        return _handle_permissions(session, 'plan')
    if cmd == 'model':
        return _handle_model(session, arg)
    if cmd == 'cost':
        return _handle_cost(session, arg)

    return (f'Unknown command: /{cmd}.\n\n{HELP}')
