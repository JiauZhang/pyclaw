from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from chatchat.core.agents import AgentDefinition

from pyclaw.config import __config_file__
from pyclaw.home import pyclaw_home

_FRONTMATTER = re.compile(r'^---\s*\n([\s\S]*?)\n---\s*\n?')

BUILT_IN = 'built-in'
USER = 'user'
PROJECT = 'project'
CLI = 'command line'

_NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$')
_NAME_RANGE = (3, 50)
_DESCRIPTION_RANGE = (10, 5_000)
_PROMPT_MIN = 20
_PROMPT_MAX = 10_000


def _user_agents_dir() -> Path:
    return pyclaw_home() / 'agents'


def agents_dir(scope: str, cwd: str) -> Path:
    if scope == PROJECT:
        return Path(cwd) / '.pyclaw' / 'agents'
    if scope == USER:
        return _user_agents_dir()
    raise ValueError(f'agents cannot be stored in the {scope} scope')


SCOPE_LABELS = {BUILT_IN: 'Bundled agents', USER: 'Your agents',
                PROJECT: 'This project', CLI: 'Given with --agents'}
SCOPE_ORDER = (CLI, USER, PROJECT, BUILT_IN)
READ_ONLY_SCOPES = frozenset({BUILT_IN, CLI})
_DISPLAY_DIRS = {USER: '~/.pyclaw/agents', PROJECT: '.pyclaw/agents'}

_READ_ONLY = frozenset({'Glob', 'Grep', 'Read', 'TaskOutput', 'TaskStop'})
_EDIT = frozenset({'Edit', 'Write'})
_EXECUTION = frozenset({'Bash'})
BUCKET_NAMES = ('Reading and search', 'Editing files', 'Running commands',
                'Other tools')


def bucket_of(name: str) -> str:
    if name in _READ_ONLY:
        return 'Reading and search'
    if name in _EDIT:
        return 'Editing files'
    if name in _EXECUTION:
        return 'Running commands'
    return 'Other tools'


def tool_buckets(names: list) -> list:
    grouped: dict = {}
    for name in names:
        grouped.setdefault(bucket_of(name), []).append(name)
    return [(label, grouped[label]) for label in BUCKET_NAMES
            if label in grouped]


def list_order(entries: list) -> list:
    ordered = []
    for scope in SCOPE_ORDER:
        ordered += sorted((e for e in entries if e.scope == scope),
                          key=lambda e: e.agent_type.lower())
    return ordered


def agent_count(entries: list) -> int:
    return sum(1 for e in entries if e.shadowed_by is None)


def model_display(defn: AgentDefinition, default: str) -> str:
    return defn.model or default


@dataclasses.dataclass
class AgentEntry:
    agent_type: str
    scope: str
    path: Path | None
    defn: AgentDefinition
    shadowed_by: str | None = None


def _parse_scalar(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in '"\'':
        return _unescape(raw[1:-1])
    return raw


def _escape(value: str) -> str:
    return (value.replace('\\', '\\\\').replace('"', '\\"')
            .replace('\n', '\\n'))


def _unescape(value: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == '\\' and index + 1 < len(value):
            out.append({'n': '\n', '\\': '\\', '"': '"'}.get(value[index + 1],
                                                              value[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER.match(text)
    if m is None:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(':')
        if key.strip():
            meta[key.strip()] = _parse_scalar(value.strip())
    return meta, text[m.end():].strip()


def _parse_tools_field(raw: str) -> list[str] | None:
    raw = (raw or '').strip()
    if not raw:
        return None
    if raw == '*':
        return ['*']
    return [t.strip() for t in raw.split(',') if t.strip()]


MEMORY_SCOPES = ('user', 'project', 'local')


def _memory_scope(raw) -> str | None:
    value = str(raw or '').strip()
    return value if value in MEMORY_SCOPES else None


def _definition_from_md(text: str, all_tools: list):
    meta, body = _parse_frontmatter(text)
    name = meta.get('name')
    if not name:
        return None
    description = meta.get('description', '')
    raw_tools = _parse_tools_field(meta.get('tools', ''))
    by_name = {t.name: t for t in all_tools}
    if raw_tools is None or raw_tools == ['*']:
        tools = list(all_tools)
    else:
        tools = [by_name[n] for n in raw_tools if n in by_name]
    return AgentDefinition(name, system_prompt=body, tools=tools,
                           model=meta.get('model') or None,
                           permission_mode=meta.get('permissionMode') or None,
                           description=description,
                           memory=_memory_scope(meta.get('memory')))


def _definition_from_path(path: Path, all_tools: list) -> AgentDefinition | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    return _definition_from_md(text, all_tools)


def discover(cwd: str, all_tools: list, cli=()) -> list[AgentEntry]:
    entries = [AgentEntry(d.agent_type, BUILT_IN, None, d)
               for d in builtin_agent_defs(all_tools)]
    for scope in (USER, PROJECT):
        try:
            files = sorted(agents_dir(scope, cwd).glob('*.md'))
        except OSError:
            files = []
        for path in files:
            defn = _definition_from_path(path, all_tools)
            if defn is not None:
                entries.append(AgentEntry(defn.agent_type, scope, path, defn))
    entries += [AgentEntry(d.agent_type, CLI, None, d) for d in cli]
    winner = {e.agent_type: e.scope for e in entries}
    for entry in entries:
        if winner[entry.agent_type] != entry.scope:
            entry.shadowed_by = winner[entry.agent_type]
    return entries


def parse_agents_json(text, all_tools: list) -> list[AgentDefinition]:
    try:
        data = json.loads(str(text or ''))
    except ValueError:
        raise ValueError('--agents is not valid JSON') from None
    if not isinstance(data, dict):
        raise ValueError('--agents must be a JSON object of name: definition')
    by_name = {t.name: t for t in all_tools}
    defs = []
    for name, spec in data.items():
        if not isinstance(spec, dict):
            raise ValueError(f'--agents: {name} must be an object')
        wanted = spec.get('tools')
        tools = (list(all_tools) if not wanted
                 else [by_name[n] for n in wanted if n in by_name])
        defs.append(AgentDefinition(
            str(name), system_prompt=str(spec.get('prompt') or ''),
            tools=tools, model=spec.get('model') or None,
            permission_mode=spec.get('permissionMode') or None,
            description=str(spec.get('description') or ''),
            memory=_memory_scope(spec.get('memory'))))
    return defs


def load_agent_defs(cwd: str, all_tools: list,
                    cli=()) -> list[AgentDefinition]:
    return [e.defn for e in discover(cwd, all_tools=all_tools, cli=cli)
            if e.shadowed_by is None]


def render_agent_md(defn: AgentDefinition, all_tools: list) -> str:
    lines = ['---', f'name: {defn.agent_type}',
             f'description: "{_escape(defn.description)}"']
    if defn.tools and {t.name for t in defn.tools} != {t.name
                                                       for t in all_tools}:
        lines.append('tools: ' + ', '.join(t.name for t in defn.tools))
    if defn.model:
        lines.append(f'model: {defn.model}')
    if defn.permission_mode:
        lines.append(f'permissionMode: {defn.permission_mode}')
    if defn.memory:
        lines.append(f'memory: {defn.memory}')
    lines += ['---', '', defn.system_prompt.strip(), '']
    return '\n'.join(lines)


def write_agent(defn: AgentDefinition, scope: str, cwd: str,
                all_tools: list, overwrite: bool = False) -> Path:
    path = agents_dir(scope, cwd) / f'{defn.agent_type}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open('w' if overwrite else 'x', encoding='utf-8')
    except FileExistsError:
        raise FileExistsError(f'an agent is already defined in {path}') from None
    with handle:
        handle.write(render_agent_md(defn, all_tools))
    return path


def remove_agent(entry: AgentEntry) -> None:
    if entry.scope == BUILT_IN or entry.path is None:
        raise ValueError('built-in agents cannot be removed')
    entry.path.unlink(missing_ok=True)


def validate_type(name: str) -> str | None:
    if not name:
        return 'An agent needs a name'
    if not _NAME.match(name):
        return ('A name is letters, digits and hyphens only, starting and '
                'ending with a letter or digit')
    if len(name) < _NAME_RANGE[0]:
        return f'A name is at least {_NAME_RANGE[0]} characters'
    if len(name) > _NAME_RANGE[1]:
        return f'A name is at most {_NAME_RANGE[1]} characters'
    return None


def relative_path(entry: AgentEntry) -> str:
    if entry.path is None:
        return SCOPE_LABELS[BUILT_IN]
    return f'{_DISPLAY_DIRS[entry.scope]}/{entry.path.name}'


def validate(defn: AgentDefinition, known_tools: list[str],
             taken: list[tuple]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    name_error = validate_type(defn.agent_type)
    if name_error:
        errors.append(name_error)
    for other, scope in taken:
        if other == defn.agent_type:
            errors.append(f'"{defn.agent_type}" is already taken by '
                          f'{SCOPE_LABELS[scope].lower()}')
    if not defn.description.strip():
        errors.append('Add a description: it is how the model picks this agent')
    elif not _DESCRIPTION_RANGE[0] <= len(defn.description) \
            <= _DESCRIPTION_RANGE[1]:
        warnings.append(f'{len(defn.description)} characters of description; '
                        f'{_DESCRIPTION_RANGE[0]}-{_DESCRIPTION_RANGE[1]} '
                        f'is easier to read')
    names = [t.name for t in defn.tools]
    if not names:
        errors.append('Select at least one tool, or this agent does nothing')
    unknown = [n for n in names if n not in known_tools]
    if unknown:
        errors.append('Unknown tools: ' + ', '.join(unknown))
    prompt = defn.system_prompt.strip()
    if not prompt:
        errors.append('A system prompt is required')
    elif len(prompt) < _PROMPT_MIN:
        errors.append(f'{len(prompt)} characters of system prompt '
                      f'is not enough')
    elif len(prompt) > _PROMPT_MAX:
        warnings.append(f'{len(prompt)} characters of system prompt ride '
                        'along on every run')
    return errors, warnings


def _statusline_prompt() -> str:
    home = __config_file__.parent
    return STATUSLINE_SYSTEM_PROMPT.format(
        config_file=__config_file__,
        script=home / 'statusline.sh',
    )


DENIED_FOR_READ_ONLY = ('Agent', 'Edit', 'Write', 'ExitPlanMode')

READ_ONLY_NOTE = """You only look. You cannot create, change, move or delete \
anything, not even in a temporary directory, and no command you run may change \
state. If the task needs a change, say what should change instead of making it."""

EXPLORE_PROMPT = """You search a codebase and report what is there.

""" + READ_ONLY_NOTE + """

- Glob finds files by pattern; Grep searches their contents; Read shows a file.
- Look in more than one place: the same thing may be spelled differently
  elsewhere.
- Report file paths with line numbers, and say how sure you are: what you
  checked, and what you did not."""

PLAN_PROMPT = """You design how a piece of work should be done, before anyone \
starts it.

""" + READ_ONLY_NOTE + """

- Read enough of the code to name the files the change touches.
- Give the steps in the order they have to happen, what each one changes, and
  the trade-offs you rejected. Say which step is riskiest."""


def builtin_agent_defs(all_tools: list) -> list[AgentDefinition]:
    by_name = {t.name: t for t in all_tools}
    readable = [t for t in all_tools if t.name not in DENIED_FOR_READ_ONLY]
    return [
        AgentDefinition(
            'statusline-setup',
            system_prompt=_statusline_prompt(),
            tools=[by_name[n] for n in ('Read', 'Edit') if n in by_name],
            description="Sets up or edits PyClaw's status line setting."),
        AgentDefinition(
            'Explore',
            system_prompt=EXPLORE_PROMPT,
            tools=readable,
            description='Fast, read-only search of the codebase: find files by '
                        'pattern, search their contents, answer questions '
                        'about how something works. Cannot change anything.'),
        AgentDefinition(
            'Plan',
            system_prompt=PLAN_PROMPT,
            tools=readable,
            description='Designs an implementation plan for a task: the steps '
                        'in order, the files each touches, the trade-offs. '
                        'Cannot change anything.'),
    ]


STATUSLINE_SYSTEM_PROMPT = '''You configure PyClaw's status line. The setting is \
the "statusLine" key of {config_file}.

To turn the user's shell prompt into a status line:
1. Look for a PS1 assignment, checking ~/.zshrc, then ~/.bashrc, \
~/.bash_profile, ~/.profile.
2. Take its value with this pattern: \
(?:^|\\n)\\s*(?:export\\s+)?PS1\\s*=\\s*["']([^"']+)["']
3. Replace every prompt escape with a command printing the same text:
   \\u -> $(whoami)     \\h -> $(hostname -s)   \\H -> $(hostname)
   \\w -> $(pwd)        \\W -> $(basename "$(pwd)")
   \\t -> $(date +%H:%M:%S)      \\d -> $(date "+%a %b %d")
   \\@ -> $(date +%I:%M%p)       \\$ -> $      \\n -> \\n      \\# -> #      \\! -> !
4. Keep the colours and emit them through printf; the row is drawn in a \
terminal that dims its colors.
5. Drop a trailing "$" or ">" from the prompt.
6. With no PS1 and nothing else to go on, ask the user what they want.

How the statusLine command works:
1. PyClaw writes this JSON to the command's stdin:
   {{
     "session_id": "string",       // one session, one id
     "transcript_path": "string",  // where the conversation is stored
     "cwd": "string",              // directory PyClaw runs in
     "permission_mode": "string",  // default | acceptEdits | plan | bypassPermissions
     "model": {{
       "id": "string",             // model id
       "display_name": "string"    // model name as shown to the user
     }},
     "workspace": {{
       "current_dir": "string",    // current directory
       "project_dir": "string",    // project root
       "added_dirs": ["string"]
     }},
     "version": "string",          // PyClaw version
     "usage": {{                   // session totals so far
       "prompt_tokens": number,
       "completion_tokens": number,
       "total_tokens": number,
       "cached_tokens": number
     }},
     "context_window": {{
       "total_input_tokens": number,
       "total_output_tokens": number,
       "context_window_size": number,      // occupancy that triggers compaction
       "current_usage": {{"input_tokens": number}},
       "used_percentage": number,          // 0-100
       "remaining_percentage": number      // 0-100
     }}
   }}

   Read a field with jq, e.g.:
   - input=$(cat); echo "$(echo "$input" | jq -r '.model.display_name') in \
$(echo "$input" | jq -r '.workspace.current_dir')"
   - input=$(cat); remaining=$(echo "$input" | jq -r \
'.context_window.remaining_percentage // empty'); [ -n "$remaining" ] && echo \
"Context: $remaining% remaining"

2. When the command outgrows a config line, save it to {script} and call that \
file from the config instead.
3. Save the setting like this:
   {{
     "statusLine": {{
       "type": "command",
       "command": "your_command_here"
     }}
   }}
   PyClaw reloads the config before each redraw, so the row changes as soon as \
the file is written.

Rules:
- Touch no other key in the config.
- When the config is a symlink, write to the file it points at.
- Report what you configured, naming the script file if you wrote one.
- Skip git's optional locks in any git command the script runs.
- Close your reply by telling the parent agent that later status line edits go \
through the "statusline-setup" agent, and that the user can keep asking for \
tweaks.
'''
