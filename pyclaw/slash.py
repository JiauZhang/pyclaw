from pathlib import Path

COMMANDS = [
    {'name': 'help', 'aliases': ('h', '?'), 'desc': 'Show this help', 'hint': ''},
    {'name': 'clear', 'desc': 'Clear the current session conversation history and token stats', 'hint': ''},
    {'name': 'init', 'desc': 'Create a PYCLAW.md project instructions file (claude /init)', 'hint': ''},
    {'name': 'memory', 'desc': 'Show loaded project memory (PYCLAW.md) locations', 'hint': ''},
    {'name': 'compact', 'desc': 'Force context compaction now (claude /compact)', 'hint': ''},
    {'name': 'status', 'desc': 'Show the current session runtime info', 'hint': ''},
    {'name': 'permissions', 'desc': 'Show/switch permission mode, manage permission rules', 'hint': '[mode|remove <rule>]'},
    {'name': 'plan', 'desc': 'Enter plan (read-only) mode', 'hint': ''},
    {'name': 'model', 'desc': 'Show or switch the model for this session', 'hint': '[name]'},
    {'name': 'cost', 'desc': 'Show token usage and estimated cost', 'hint': ''},
]


def _help_text() -> str:
    lines = ['Available commands:']
    for cmd in COMMANDS:
        name = f"/{cmd['name']}" + ''.join(f" or /{a}"
                                           for a in cmd.get('aliases', ()))
        lines.append(f'{name:<14}{cmd["desc"]}')
    return '\n'.join(lines)


HELP = _help_text()


def suggest(text: str) -> list[dict]:
    """claude 的命令建议（最小子集）：输入以 / 开头且未带实参时，按
    精确名 > 精确别名 > 前缀名 > 前缀别名 > 名字/描述子串排序返回。
    大小写不敏感；命令后已输入实参则隐藏菜单。"""
    if not text.startswith('/'):
        return []
    query = text[1:]
    if query != query.strip() or ' ' in query.strip():
        return []
    query = query.strip().lower()
    if not query:
        return list(COMMANDS)
    exact = [c for c in COMMANDS if c['name'] == query]
    prefix_name = [c for c in COMMANDS
                   if c['name'].startswith(query) and c not in exact]
    prefix_alias = [c for c in COMMANDS
                    if c not in exact + prefix_name
                    and any(a.startswith(query) for a in c.get('aliases', ()))]
    substring = [c for c in COMMANDS
                 if c not in exact + prefix_name + prefix_alias
                 and (query in c['name'].lower()
                      or query in c['desc'].lower())]
    return exact + prefix_name + prefix_alias + substring


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
    if cmd == 'init':
        from pyclaw.agent_memory import init_project_memory
        return init_project_memory(getattr(session, 'cwd', None) or '.')
    if cmd == 'compact':
        compactor = getattr(session, 'compact', None)
        if compactor is None:
            return 'Compaction is not available for this session.'
        return await compactor()
    if cmd == 'memory':
        from pyclaw.agent_memory import load_project_memory, _user_memory_file
        cwd = getattr(session, 'cwd', None) or '.'
        lines = [f'user: {_user_memory_file()}',
                 f'project: {Path(cwd) / "PYCLAW.md"}']
        memory = load_project_memory(cwd)
        lines.append('loaded: yes' if memory else 'loaded: nothing found')
        return '\n'.join(lines)
    if cmd == 'clear':
        session.reset()
        return 'Conversation history cleared.'
    if cmd == 'status':
        return _status(session, session_key)
    if cmd == 'permissions':
        return _handle_permissions(session, arg)
    if cmd == 'plan':
        return _handle_permissions(session, 'plan')
    if cmd == 'model':
        return _handle_model(session, arg)
    if cmd == 'cost':
        return _handle_cost(session, arg)

    return (f'Unknown command: /{cmd}.\n\n{HELP}')
