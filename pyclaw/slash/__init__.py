import difflib
from pathlib import Path

from pyclaw.slash.session import (_handle_agents, _handle_branch, _handle_debug,
                                  _handle_hooks, _handle_permissions,
                                  _handle_plan, _handle_rename,
                                  _handle_resume, _handle_rewind,
                                  _handle_skills, _handle_tasks, _status)
from pyclaw.slash.setup import _handle_effort, _handle_statusline
from pyclaw.slash.text import _copy, _export
from pyclaw.slash.usage import (_context, _handle_cost, _handle_model,
                                _stats_lines, _usage_lines)

from pyclaw.team import memory as agent_memory

COMMANDS = [
    {'name': 'help', 'aliases': ('h', '?'), 'desc': 'List every command', 'hint': ''},
    {'name': 'clear', 'desc': 'Start a new session, keeping the old transcript', 'hint': ''},
    {'name': 'resume', 'desc': 'Pick a saved conversation, or name the one to '
                               'continue', 'hint': '[name or id]'},
    {'name': 'rename', 'desc': 'Name this conversation, so /resume is readable',
     'hint': '[name]'},
    {'name': 'branch', 'desc': 'Carry on in a copy of this conversation from '
                               'here', 'hint': '[name]'},
    {'name': 'rewind', 'desc': 'Put the files and conversation back to an '
                               'earlier turn',
     'hint': '[n] [code|conversation]'},
    {'name': 'diff', 'desc': 'Show what has changed in the working tree',
     'hint': ''},
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
    {'name': 'debug', 'desc': 'Start recording every event and read what this '
                              'session has run into so far',
     'hint': '[what went wrong]'},
    {'name': 'context', 'desc': 'Show what the model is sent and how full the '
                                'window is', 'hint': ''},
    {'name': 'permissions', 'desc': 'Show/switch permission mode, manage permission rules', 'hint': '[mode|remove <rule>]'},
    {'name': 'agents', 'desc': 'List and manage the agent definitions PyClaw can delegate to', 'hint': ''},
    {'name': 'plan', 'desc': 'Enter plan (read-only) mode', 'hint': ''},
    {'name': 'model', 'desc': 'Show or switch the model for this session', 'hint': '[name]'},
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
    {'name': 'export', 'desc': 'Write this conversation out as a markdown '
                               'file', 'hint': '[name.md]'},
    {'name': 'copy', 'desc': 'Put an answer, or one of its code blocks, on '
                             'the clipboard', 'hint': '[n[:m]]'},
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

async def handle_slash(text: str, session, session_key: str = '',
                       terminal=None) -> str | None | tuple:
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
        cwd = getattr(session, 'cwd', None) or '.'
        lines = [f'user: {agent_memory._user_memory_file()}'
                 f'project: {Path(cwd) / "AGENTS.md"}']
        memory = agent_memory.load_project_memory(cwd)
        lines.append('loaded: yes' if memory else 'loaded: nothing found')
        for rule in agent_memory.rule_set(cwd).all():
            lines.append(f'rule for {", ".join(rule.globs)}: {rule.path}')
        return '\n'.join(lines)
    if cmd == 'clear':
        await session.end_session('clear')
        session.reset()
        return 'Conversation history cleared. Started a new session.'
    if cmd == 'resume':
        return _handle_resume(session, arg)
    if cmd == 'rename':
        return _handle_rename(session, arg)
    if cmd == 'branch':
        return _handle_branch(session, arg)
    if cmd == 'rewind':
        return _handle_rewind(session, arg)
    if cmd in ('tasks', 'bashes'):
        return _handle_tasks(session, arg)
    if cmd == 'skills':
        return _handle_skills(session, arg)
    if cmd == 'hooks':
        return _handle_hooks(session, arg)
    if cmd == 'debug':
        return _handle_debug(session, arg)
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
    if cmd == 'effort':
        return await _handle_effort(session, arg)
    if cmd == 'cost':
        return _handle_cost(session, arg)
    if cmd == 'usage':
        return _usage_lines(session, arg)
    if cmd == 'export':
        return _export(session, arg, session_key)
    if cmd == 'copy':
        return _copy(session, arg, terminal=terminal)
    if cmd == 'stats':
        return _stats_lines(arg)
    if cmd == 'context':
        return _context(session, arg)
    if cmd == 'statusline':
        return _handle_statusline(arg)

    return (f'Unknown command: /{cmd}.\n\n{HELP}')
