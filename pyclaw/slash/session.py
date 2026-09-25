# Session commands: what the conversation is, and where it stands.
from pyclaw.tui.formatting import _plural

from pyclaw.slash.usage import _cost_of

from pyclaw.cost import format_cost
from pyclaw import session_store
from pyclaw.version import __version__


def _status(session, session_key: str) -> str:
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
    if not arg:
        sessions = session_store.list_sessions()
        if not sessions:
            return 'No saved sessions.'
        lines = ['Saved sessions:']
        for item in sessions[:20]:
            name = f"  {item['title']}" if item.get('title') else ''
            lines.append(f"  {item['id']}  ({item['messages']} messages)"
                         + name)
        lines.append('Use /resume <id> to continue one of them.')
        return '\n'.join(lines)
    resumed = getattr(session, 'resume_session', None)
    count = resumed(arg) if resumed else 0
    if not count:
        return f'No transcript found for session: {arg}'
    return f'Resumed {count} messages from {arg}.'

def _handle_rename(session, arg: str) -> str:
    current = getattr(session, 'title', '')
    if not arg:
        return (f'This conversation is called "{current}".'
                if current else
                'This conversation has no name yet; /rename <name> gives it '
                'one.')
    try:
        title = session.rename(arg)
    except ValueError as exc:
        return f'Error: {exc}'
    return f'This conversation is now called "{title}".'


def _handle_branch(session, arg: str) -> str:
    before = session.conv_session_id
    try:
        fork = session.branch(arg)
    except ValueError as exc:
        return f'Error: {exc}'
    return (f'Branched conversation into "{fork["title"]}" '
            f'({fork["messages"]} messages). You are now in the branch.\n'
            f'To go back to the original: /resume {before}')


async def _handle_plan(session, arg: str):
    if arg == 'open':
        return 'No plan file to open: pyclaw keeps the plan in the conversation.'
    message = await _handle_permissions(session, 'plan')
    if arg:
        return f'{message}\nPlan goal: {arg}', arg
    return message


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
    files = _plural(len(result['files']), 'file')
    messages = _plural(result['messages'], 'message')
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
