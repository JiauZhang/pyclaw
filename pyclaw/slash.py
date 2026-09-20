import difflib
from pathlib import Path

COMMANDS = [
    {'name': 'help', 'aliases': ('h', '?'), 'desc': 'List every command', 'hint': ''},
    {'name': 'clear', 'desc': 'Start a new session, keeping the old transcript', 'hint': ''},
    {'name': 'resume', 'desc': 'List saved sessions or load one with /resume <id>', 'hint': '[id]'},
    {'name': 'init', 'desc': 'Generate an AGENTS.md by surveying the codebase', 'hint': ''},
    {'name': 'memory', 'desc': 'Show loaded project memory (AGENTS.md) locations', 'hint': ''},
    {'name': 'compact', 'desc': 'Force context compaction now', 'hint': ''},
    {'name': 'status', 'desc': 'Show the current session runtime info', 'hint': ''},
    {'name': 'permissions', 'desc': 'Show/switch permission mode, manage permission rules', 'hint': '[mode|remove <rule>]'},
    {'name': 'agents', 'desc': 'List and manage the agent definitions PyClaw can delegate to', 'hint': ''},
    {'name': 'plan', 'desc': 'Enter plan (read-only) mode', 'hint': ''},
    {'name': 'model', 'desc': 'Show or switch the model for this session', 'hint': '[name]'},
    {'name': 'cost', 'desc': 'Show token usage and estimated cost', 'hint': ''},
    {'name': 'statusline', 'desc': "Set up PyClaw's status line",
     'hint': '[instructions]'},
]


def _help_text() -> str:
    lines = ['Commands:']
    for cmd in COMMANDS:
        name = f"/{cmd['name']}" + ''.join(f" or /{a}"
                                           for a in cmd.get('aliases', ()))
        lines.append(f'{name:<14}{cmd["desc"]}')
    return '\n'.join(lines)


HELP = _help_text()

INIT_PROMPT = '''Analyze this codebase and create an AGENTS.md file, which will be given to future instances of PyClaw (and any AGENTS.md-aware agent) to operate in this repository.

What to add:
1. Commands that will be commonly used, such as how to build, lint, and run tests. Include the necessary commands to develop in this codebase, such as how to run a single test.
2. High-level code architecture and structure so that future instances can be productive more quickly. Focus on the "big picture" architecture that requires reading multiple files to understand.

Usage notes:
- If there's already an AGENTS.md, improve it (edit it) rather than overwriting blindly.
- When you create the initial AGENTS.md, do not repeat yourself and do not include obvious instructions like "Provide helpful error messages to users", "Write unit tests for all new utilities", "Never include sensitive information (API keys, tokens) in code or commits".
- Avoid listing every component or file structure that can be easily discovered.
- Don't include generic development practices.
- If there is a README.md, make sure to include the important parts.
- Do not make up information such as "Common Development Tasks", "Tips for Development", "Support and Documentation" unless this is expressly included in other files that you read.
- Be sure to prefix the file with:

# AGENTS.md

This file provides guidance to PyClaw (and any AGENTS.md-aware agent) when working with code in this repository.
'''

_USAGE: dict[str, int] = {}


def _fuzzy_score(query: str, text: str) -> float:
    q, t = query.lower(), text.lower()
    if not q or len(q) > len(t):
        return 0.0
    if q in t:
        return 1.0
    if len(q) < 3:
        return 0.0
    it = iter(t)
    if not all(ch in it for ch in q):
        return 0.0
    return difflib.SequenceMatcher(None, q, t).ratio()


def suggest(text: str) -> list[dict]:
    if not text.startswith('/'):
        return []
    query = text[1:]
    if query != query.strip() or ' ' in query.strip():
        return []
    query = query.strip().lower()
    if not query:
        used = [c for c in COMMANDS if _USAGE.get(c['name'])]
        used.sort(key=lambda c: _USAGE[c['name']], reverse=True)
        top = used[:5]
        rest = [c for c in COMMANDS if c not in top]
        rest.sort(key=lambda c: c['name'])
        return top + rest
    scored = []
    for c in COMMANDS:
        name = c['name']
        aliases = list(c.get('aliases', ()))
        exact = name == query
        alias_exact = any(a == query for a in aliases)
        prefix = name.startswith(query)
        prefix_alias = min((len(a) for a in aliases
                            if a.startswith(query)), default=0)
        fuzzy = max(_fuzzy_score(query, name),
                    max((_fuzzy_score(query, a) for a in aliases),
                        default=0.0),
                    _fuzzy_score(query, c['desc']))
        if not (exact or alias_exact or prefix or prefix_alias or fuzzy > 0):
            continue
        if exact:
            rank = (0, 0, 0, name)
        elif alias_exact:
            rank = (1, 0, 0, name)
        elif prefix:
            rank = (2, len(name), name, '')
        elif prefix_alias:
            rank = (3, prefix_alias, name, '')
        else:
            rank = (4, -fuzzy, -_USAGE.get(name, 0), name)
        scored.append((c, rank))
    scored.sort(key=lambda t: t[1])
    return [t[0] for t in scored]


def _pricing() -> dict:
    from pyclaw.config import load as load_config
    return load_config().get('pricing') or {}


def _cost_of(session):
    from pyclaw.cost import usage_cost
    return usage_cost(session.model, session.usage, _pricing())


def _status(session, session_key: str) -> str:
    from pyclaw.cost import format_cost
    from pyclaw.version import __version__
    usage = session.usage
    return (
        f"pyclaw: {__version__}\n"
        f"Session: {session_key or session.name}\n"
        f"Directory: {getattr(session, 'cwd', '')}\n"
        f"Mode: {session.mode}\n"
        f"Provider: {session.provider}\n"
        f"Model: {session.model}\n"
        f"Thinking: {'on' if session.thinking else 'off'}\n"
        f"Context messages: {session.context_messages}\n"
        f"Active sub-agents: {session.active_agents}\n"
        f"Loaded tools: {len(session.available_tools)}\n"
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


def _handle_agents(session) -> str:
    agents = getattr(session, 'agent_types', None) or []
    if not agents:
        return 'No agent definitions loaded.'
    lines = ['Loaded agents:']
    lines += [f'  {name}: {desc}' for name, desc in agents]
    lines.append('Create, edit and delete them from the terminal UI with '
                 '/agents.')
    return '\n'.join(lines)


def _handle_resume(session, arg: str) -> str:
    from pyclaw.agents import list_sessions
    if not arg:
        sessions = list_sessions()
        if not sessions:
            return 'No saved sessions.'
        lines = ['Saved sessions:']
        for item in sessions[:20]:
            lines.append(f"  {item['id']}  ({item['messages']} messages)")
        lines.append('Use /resume <id> to continue one of them.')
        return '\n'.join(lines)
    resumed = getattr(session, 'resume_session', None)
    count = resumed(arg) if resumed else 0
    if not count:
        return f'No transcript found for session: {arg}'
    return f'Resumed {count} messages from {arg}.'


def _handle_plan(session, arg: str):
    if arg == 'open':
        return 'No plan file to open: pyclaw keeps the plan in the conversation.'
    message = _handle_permissions(session, 'plan')
    if arg:
        return f'{message}\nPlan goal: {arg}', arg
    return message


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


STATUSLINE_PROMPT = 'Set up my status line from my shell PS1 configuration'


def _handle_statusline(arg: str) -> tuple:
    prompt = arg or STATUSLINE_PROMPT
    return ('Setting up the status line…',
            f'Create an agent with create_agent, subagent_type '
            f'"statusline-setup" and the prompt "{prompt}"')


async def handle_slash(text: str, session, session_key: str = '') -> str | None | tuple:
    text = text.strip()
    if not text.startswith('/'):
        return None
    cmd, _, arg = text[1:].partition(' ')
    cmd = cmd.strip().lower()
    arg = arg.strip()

    for entry in COMMANDS:
        if cmd == entry['name'] or cmd in entry.get('aliases', ()):
            _USAGE[entry['name']] = _USAGE.get(entry['name'], 0) + 1
            break

    if cmd in ('help', 'h', '?'):
        return HELP
    if cmd == 'init':
        cwd = getattr(session, 'cwd', None) or '.'
        existing = Path(cwd) / 'AGENTS.md'
        if existing.exists():
            info = (f'AGENTS.md already exists at {existing}. '
                    f'Asking the agent to improve it.')
        else:
            info = 'Surveying the codebase and drafting AGENTS.md…'
        return info, INIT_PROMPT
    if cmd == 'compact':
        compactor = getattr(session, 'compact', None)
        if compactor is None:
            return 'Compaction is not available for this session.'
        return await compactor()
    if cmd == 'memory':
        from pyclaw.agent_memory import load_project_memory, _user_memory_file
        cwd = getattr(session, 'cwd', None) or '.'
        lines = [f'user: {_user_memory_file()}',
                 f'project: {Path(cwd) / "AGENTS.md"}']
        memory = load_project_memory(cwd)
        lines.append('loaded: yes' if memory else 'loaded: nothing found')
        return '\n'.join(lines)
    if cmd == 'clear':
        await session.end_session('clear')
        session.reset()
        return 'Conversation history cleared. Started a new session.'
    if cmd == 'resume':
        return _handle_resume(session, arg)
    if cmd == 'status':
        return _status(session, session_key)
    if cmd == 'permissions':
        return _handle_permissions(session, arg)
    if cmd == 'agents':
        return _handle_agents(session)
    if cmd == 'plan':
        return _handle_plan(session, arg)
    if cmd == 'model':
        return _handle_model(session, arg)
    if cmd == 'cost':
        return _handle_cost(session, arg)
    if cmd == 'statusline':
        return _handle_statusline(arg)

    return (f'Unknown command: /{cmd}.\n\n{HELP}')
