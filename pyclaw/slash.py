import difflib
from pathlib import Path

COMMANDS = [
    {'name': 'help', 'aliases': ('h', '?'), 'desc': 'List every command', 'hint': ''},
    {'name': 'clear', 'desc': 'Start a new session, keeping the old transcript', 'hint': ''},
    {'name': 'resume', 'desc': 'List saved sessions or load one with /resume <id>', 'hint': '[id]'},
    {'name': 'rewind', 'desc': 'Put the files and conversation back to an '
                               'earlier turn',
     'hint': '[n] [code|conversation]'},
    {'name': 'tasks', 'aliases': ('bashes',),
     'desc': 'List what is running in the background', 'hint': ''},
    {'name': 'skills', 'desc': 'List the skills PyClaw can load on demand',
     'hint': ''},
    {'name': 'hooks', 'desc': 'List the hooks that will run and where they '
                              'come from', 'hint': ''},
    {'name': 'init', 'desc': 'Generate an AGENTS.md by surveying the codebase', 'hint': ''},
    {'name': 'memory', 'desc': 'Show loaded project memory (AGENTS.md) locations', 'hint': ''},
    {'name': 'compact', 'desc': 'Force context compaction now', 'hint': ''},
    {'name': 'status', 'desc': 'Show the current session runtime info', 'hint': ''},
    {'name': 'context', 'desc': 'Show what the model is sent and how full the '
                                'window is', 'hint': ''},
    {'name': 'permissions', 'desc': 'Show/switch permission mode, manage permission rules', 'hint': '[mode|remove <rule>]'},
    {'name': 'agents', 'desc': 'List and manage the agent definitions PyClaw can delegate to', 'hint': ''},
    {'name': 'plan', 'desc': 'Enter plan (read-only) mode', 'hint': ''},
    {'name': 'model', 'desc': 'Show or switch the model for this session', 'hint': '[name]'},
    {'name': 'thinking',
     'desc': 'Show or set how much the model may reason: off, on, adaptive, '
             'or a token budget', 'hint': '[off|on|adaptive|tokens]'},
    {'name': 'effort', 'desc': 'Show or set the reasoning effort',
     'hint': '[low|medium|high|auto]'},
    {'name': 'cost', 'desc': 'Show token usage and cost at your configured rates',
     'hint': ''},
    {'name': 'usage', 'desc': 'What the recorded turns cost: today or week',
     'hint': '[today|week]'},
    {'name': 'stats', 'desc': 'Per-day totals of the recorded work',
     'hint': '[days]'},
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

INIT_PROMPT = '''Analyze this codebase and write an AGENTS.md file: the instructions AI agents read before working in this repository.

What to add:
1. The commands that matter here, such as how to build, lint, and run tests, including how to run a single test.
2. The architecture that only emerges from reading several files at once: where work enters, which layer owns what, what a change in one place forces in another.

Every line has to pass one test: would removing it make an agent get this wrong? If not, cut it.

Usage notes:
- If an AGENTS.md already exists, read it and improve it in place.
- If there is a README.md, make sure to include the important parts.
- Do not pad it with advice that is true of every repository, such as "write unit tests for new utilities", "return helpful error messages", or "never include secrets in commits".
- Do not list the file tree or restate conventions the language already defines.
- Do not make up sections such as "Common Development Tasks", "Tips for Development", or "Support and Documentation" unless a file you read already had them.
- Be specific: "use 2-space indentation in JSON configs" rather than "format code properly".

Start the file with:

# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.
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
    lines = [f'pyclaw: {__version__}',
             f'Session: {session_key or session.name}',
             f'Directory: {getattr(session, "cwd", "")}',
             f'Mode: {session.mode}']
    team = getattr(session, 'team_context', None)
    if team:
        lines.append(f'Team: {team["name"]}')
    worktree = getattr(session, 'worktree', None)
    if worktree:
        lines.append(f'Worktree: {worktree["branch"]} at {worktree["path"]}')
    lines += [f'Provider: {session.provider}',
              f'Model: {session.model}',
              f'Reasoning: {session.thinking.label()}',
              f'Context messages: {session.context_messages}',
              f'Active sub-agents: {session.active_agents}',
              f'Loaded tools: {len(session.available_tools)}',
              f'Usage: {usage.prompt_tokens} in / {usage.completion_tokens} '
              f'out / {usage.total_tokens} total',
              f'Cost: {format_cost(_cost_of(session))}']
    return '\n'.join(lines)


async def _handle_permissions(session, arg: str) -> str:
    if arg.startswith('remove '):
        rule = arg[len('remove '):].strip()
        if not rule:
            return 'Usage: /permissions remove <rule>'
        remover = getattr(session, 'remove_rule', None)
        if remover is None or not remover(rule):
            return f'Rule not found or read-only: {rule}'
        await session.note_config_change('permissions')
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


async def _handle_plan(session, arg: str):
    if arg == 'open':
        return 'No plan file to open: pyclaw keeps the plan in the conversation.'
    message = await _handle_permissions(session, 'plan')
    if arg:
        return f'{message}\nPlan goal: {arg}', arg
    return message


def _count(n: int, word: str) -> str:
    return f'{n} {word}' if n == 1 else f'{n} {word}s'


def _first_line(text) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()[:70]
    return ''


def _turn_list(turns) -> str:
    lines = ['Turns PyClaw can go back to:']
    for index, (_mark, prompt) in enumerate(turns, start=1):
        lines.append(f'  {index}. {_first_line(prompt)}')
    lines.append('/rewind <n> gives both back; add code or conversation '
                 'for one of them.')
    return '\n'.join(lines)


def _handle_rewind(session, arg: str) -> str:
    turns = session.turns()
    if not turns:
        return ('PyClaw is not keeping file history in this session, so '
                'there is nothing to rewind to.')
    parts = arg.split()
    if not parts:
        return _turn_list(turns)
    if not parts[0].isdigit():
        return 'Usage: /rewind <n> [code|conversation]'
    index = int(parts[0])
    if not 1 <= index <= len(turns):
        return f'There is no turn {index} to go back to.'
    mode = parts[1] if len(parts) > 1 else 'both'
    if mode not in ('both', 'code', 'conversation'):
        return 'Usage: /rewind <n> [code|conversation]'
    result = session.rewind(turns[index - 1][0], code=mode != 'conversation',
                            conversation=mode != 'code')
    files = _count(len(result['files']), 'file')
    messages = _count(result['messages'], 'message')
    if mode == 'code':
        return f'Put the files of turn {index} back: {files} restored.'
    if mode == 'conversation':
        return f'Put the conversation back to turn {index}: {messages} dropped.'
    return (f'Went back to turn {index}: {files} put back, '
            f'{messages} dropped.')


def _handle_hooks(session, arg: str) -> str:
    rows = session.hook_rows()
    if not rows:
        return ('No hooks are configured. Put them under "hooks" in '
                '.pyclaw/settings.json or register them from code.')
    lines = ['Hooks PyClaw will run:']
    for row in rows:
        detail = f" {row['detail']}" if row['detail'] else ''
        lines.append(f"  {row['event']} [{row['matcher']}] "
                     f"({row['type']}, {row['source']}){detail}")
    return '\n'.join(lines)


def _handle_skills(session, arg: str) -> str:
    rows = session.skill_rows()
    problems = session.skill_problems()
    lines = []
    if rows:
        lines.append('Skills:')
        for row in rows:
            line = f"  {row['name']} ({row['source']}) - {row['description']}"
            if row['allowed_tools']:
                line += f" \u00b7 allows {', '.join(row['allowed_tools'])}"
            lines.append(line)
    else:
        lines.append('No skills are installed.')
        lines.append('Put a SKILL.md with a name and description under '
                     '.pyclaw/skills/<name>/ in this project or in the PyClaw '
                     'home.')
    if problems:
        lines.append('Skipped:')
        lines.extend(f'  {problem}' for problem in problems)
    return '\n'.join(lines)


def _handle_tasks(session, arg: str) -> str:
    rows = session.task_rows()
    if not rows:
        return 'No background tasks are running.'
    lines = ['Running in the background:']
    for row in rows:
        marker = row['id'] if row['kind'] == 'shell' else ''
        lines.append(f'  {marker} {row["label"]} · {row["detail"]}'.rstrip())
    lines.append('The terminal builds the same list as a panel that can stop '
                 'them: /tasks.')
    return '\n'.join(lines)


REASONING_HINT = ('thinking takes off, on, adaptive, or a number of tokens; '
                  'effort takes low, medium, high, or auto to leave it to the '
                  'model')


def _history_window(arg: str) -> tuple[int, str]:
    if str(arg or '').strip().lower() in ('day', 'today'):
        return 1, 'today'
    if str(arg or '').strip().lower() in ('week',):
        return 7, 'last 7 days'
    if str(arg or '').strip().isdigit():
        return int(arg), f'last {arg} days'
    return 1, 'today'


def _history_cost(rows: list, pricing) -> float | None:
    from pyclaw.cost import usage_cost

    total = 0.0
    priced = False
    for row in rows:
        cost = usage_cost(row.get('model'), row, pricing)
        if cost is not None:
            total += cost
            priced = True
    return total if priced else None


def _ms(ms: int) -> str:
    return f'{int(ms) / 1000:.1f}s'


def _usage_lines(session, arg: str) -> str:
    from pyclaw.cost import format_cost
    from pyclaw.tui.formatting import _format_count
    from pyclaw.usage_history import read_days, totals

    days, label = _history_window(arg)
    rows = read_days(days)
    if not rows:
        return f'Usage \u00b7 {label}\nNothing recorded yet.'
    seen = totals(rows)
    return '\n'.join([
        f'Usage \u00b7 {label}',
        (f'tokens {_format_count(seen["total"])} \u00b7 input: '
         f'{seen["input"]}  output: {seen["output"]}  cache read: '
         f'{seen["cached"]}'),
        (f'{_count(seen["turns"], "turn")} \u00b7 '
         f'{_count(seen["tool_calls"], "tool call")} \u00b7 '
         f'{_ms(seen["tool_ms"])} in tools \u00b7 '
         f'{_ms(seen["api_ms"])} with the model'),
        (f'{seen["lines_added"]} lines added \u00b7 '
         f'{seen["lines_removed"]} lines removed \u00b7 '
         f'{_count(seen["hooks"], "hook run")} \u00b7 '
         f'{_count(seen["denials"], "refused call")}'),
        f'cost: {format_cost(_history_cost(rows, _pricing()))} at your '
        f'configured rates'])


def _stats_lines(arg: str) -> str:
    from pyclaw.usage_history import by_day, read_days

    days = int(arg) if str(arg or '').strip().isdigit() else 7
    rows = read_days(days)
    if not rows:
        return 'Stats\nNothing recorded yet.'
    lines = [f'Stats \u00b7 last {days} days']
    for day, seen in by_day(rows):
        lines.append(f'{day} \u00b7 in {seen["input"]} \u00b7 out '
                     f'{seen["output"]} \u00b7 {seen["tool_calls"]} tool '
                     f'calls \u00b7 {seen["api_ms"] / 1000:.1f}s with the '
                     f'model')
    return '\n'.join(lines)


def _reasoning(session, mode=None, budget=None, effort=None):
    from chatchat.core.thinking import Thinking

    current = session.thinking
    try:
        setting = Thinking(
            mode=current.mode if mode is None else mode,
            budget=current.budget if budget is None else budget,
            effort=current.effort if effort is None else effort)
    except ValueError:
        return None
    session.set_thinking(setting)
    return setting


async def _handle_thinking(session, arg: str) -> str:
    from . import config

    arg = arg.strip()
    if not arg:
        return session.thinking.label()
    mode, budget = (None, int(arg)) if arg.isdigit() else (arg, None)
    setting = _reasoning(session, mode=mode, budget=budget)
    if setting is None:
        return REASONING_HINT
    saved = config.load()
    saved['thinking'] = {'mode': session.thinking.mode,
                         'budget': session.thinking.budget,
                         'effort': session.thinking.effort}
    config.save(saved)
    await session.note_config_change('settings')
    return f'Thinking: {session.thinking.label()}'


async def _handle_effort(session, arg: str) -> str:
    from . import config

    arg = arg.strip()
    if not arg:
        return (f'effort {session.thinking.effort}'
                if session.thinking.effort else
                'effort auto (the model decides)')
    setting = _reasoning(session, effort='' if arg == 'auto' else arg)
    if setting is None:
        return REASONING_HINT
    saved = config.load()
    saved['thinking'] = {'mode': session.thinking.mode,
                         'budget': session.thinking.budget,
                         'effort': session.thinking.effort}
    config.save(saved)
    await session.note_config_change('settings')
    return f'Effort: {session.thinking.label()}'


async def _handle_model(session, arg: str) -> str:
    if not arg:
        return f'Model: {session.model}'
    session.set_model(arg)
    from pyclaw import load as load_config
    from pyclaw.config import save as save_config
    config = load_config()
    config['model'] = session.model
    save_config(config)
    await session.note_config_change('config')
    return f'Model: {session.model}'


def _agent_cost(model: str, usage) -> str:
    from pyclaw.cost import format_cost, usage_cost
    cost = usage_cost(model, usage, _pricing())
    price = format_cost(cost) + ('' if cost is not None else ' unpriced')
    return (f'{usage.prompt_tokens} in / {usage.completion_tokens} out'
            f' / {usage.total_tokens} tokens \u00b7 {price}')


def _handle_cost(session, arg: str) -> str:
    from pyclaw.cost import format_cost
    usage = session.usage
    cost = _cost_of(session)
    detail = format_cost(cost)
    if cost is None:
        detail += f' (add pricing.{session.model} to config)'
    lines = [f"Model: {session.model}",
             f"Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out"
             f" / {usage.total_tokens} total",
             f"Cost: {detail}"]
    agents = session.agent_usage()
    if len(agents) > 1:
        lines += ['', 'By agent:'] + [
            f'@{name} \u00b7 {model or session.model} \u00b7 '
            f'{_agent_cost(model or session.model, agent_usage)}'
            for name, model, agent_usage in agents]
    return '\n'.join(lines)


def _context(session, arg: str) -> str:
    import json

    def size(value) -> int:
        return len(str(value if value is not None else ''))

    window = int(session.context_window or 0)
    used = int(session.used_context)
    out = [f'Context for {session.model}']
    if window:
        out.append(f'Measured: {used:,} of {window:,} tokens '
                   f'({round(used / window * 100)}%) '
                   f'\u00b7 {max(0, window - used):,} free')
        out.append(f'Auto-compact at {int(session.compact_threshold):,} tokens')
    else:
        out.append(f'Measured: {used:,} tokens \u00b7 no window configured')

    tools = sorted(((str(schema.get('name', '')),
                     size(json.dumps(schema, sort_keys=True))
                     + size(schema.get('description', '')))
                    for schema in session.tool_schemas()),
                   key=lambda row: -row[1])
    memory = [str(item.get('path') or '') for item in session.instruction_files
              if isinstance(item, dict)]
    agents = list(session.agent_types)
    transcript = session.transcript()

    out += ['', 'What the model is sent, in characters:',
            f'  System prompt:  {size(session.lead_instruction):,}',
            f'  Memory files:   {len(memory)}']
    out += [f'    {path}' for path in memory if path]
    out.append(f'  Tools:          {len(tools)}')
    out += [f'    {name} {chars:,}' for name, chars in tools[:5]]
    out.append(f'  Agents:         {len(agents)}')
    out += [f'    {name}' for name, _ in agents]
    out.append(f'  Conversation:   {session.context_messages} messages '
               f'({sum(size(message.get("content")) for message in transcript):,}'
               f' chars)')
    return '\n'.join(out)


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
    if cmd == 'rewind':
        return _handle_rewind(session, arg)
    if cmd in ('tasks', 'bashes'):
        return _handle_tasks(session, arg)
    if cmd == 'skills':
        return _handle_skills(session, arg)
    if cmd == 'hooks':
        return _handle_hooks(session, arg)
    if cmd == 'status':
        return _status(session, session_key)
    if cmd == 'permissions':
        return await _handle_permissions(session, arg)
    if cmd == 'agents':
        return _handle_agents(session)
    if cmd == 'plan':
        return await _handle_plan(session, arg)
    if cmd == 'model':
        return await _handle_model(session, arg)
    if cmd == 'thinking':
        return await _handle_thinking(session, arg)
    if cmd == 'effort':
        return await _handle_effort(session, arg)
    if cmd == 'cost':
        return _handle_cost(session, arg)
    if cmd == 'usage':
        return _usage_lines(session, arg)
    if cmd == 'stats':
        return _stats_lines(arg)
    if cmd == 'context':
        return _context(session, arg)
    if cmd == 'statusline':
        return _handle_statusline(arg)

    return (f'Unknown command: /{cmd}.\n\n{HELP}')
