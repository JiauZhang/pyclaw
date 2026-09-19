from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from chatchat.core.agents import AgentDefinition

_FRONTMATTER = re.compile(r'^---\s*\n([\s\S]*?)\n---\s*\n?')

BUILT_IN = 'built-in'
USER = 'user'
PROJECT = 'project'

_NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$')
_NAME_RANGE = (3, 50)
_DESCRIPTION_RANGE = (10, 5_000)
_PROMPT_MIN = 20
_PROMPT_MAX = 10_000


def _user_agents_dir() -> Path:
    from pyclaw import __pyclaw_home__
    return Path(__pyclaw_home__) / 'agents'


def agents_dir(scope: str, cwd: str) -> Path:
    if scope == PROJECT:
        return Path(cwd) / '.pyclaw' / 'agents'
    if scope == USER:
        return _user_agents_dir()
    raise ValueError(f'agents cannot be stored in the {scope} scope')


SCOPE_LABELS = {BUILT_IN: 'Built-in agents', USER: 'User agents',
                PROJECT: 'Project agents'}
SCOPE_ORDER = (USER, PROJECT, BUILT_IN)
_DISPLAY_DIRS = {USER: '~/.pyclaw/agents', PROJECT: '.pyclaw/agents'}

_READ_ONLY = frozenset({'Glob', 'Grep', 'Read', 'TaskOutput', 'TaskStop'})
_EDIT = frozenset({'Edit', 'Write', 'MultiEdit'})
_EXECUTION = frozenset({'Bash'})
BUCKET_NAMES = ('Read-only tools', 'Edit tools', 'Execution tools',
                'Other tools')


def bucket_of(name: str) -> str:
    if name in _READ_ONLY:
        return 'Read-only tools'
    if name in _EDIT:
        return 'Edit tools'
    if name in _EXECUTION:
        return 'Execution tools'
    return 'Other tools'


def tool_buckets(names: list) -> list:
    """(bucket name, tool names) in display order; empty buckets dropped."""
    grouped: dict = {}
    for name in names:
        grouped.setdefault(bucket_of(name), []).append(name)
    return [(label, grouped[label]) for label in BUCKET_NAMES
            if label in grouped]


def list_order(entries: list) -> list:
    """Rows in the order the panel shows them: user, then project, then
    built-in, each group alphabetical."""
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
                           description=description)


def _definition_from_path(path: Path, all_tools: list) -> AgentDefinition | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    return _definition_from_md(text, all_tools)


def discover(cwd: str, all_tools: list) -> list[AgentEntry]:
    """Every agent definition on disk, in load order, with the loser marked."""
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
    winner = {e.agent_type: e.scope for e in entries}
    for entry in entries:
        if winner[entry.agent_type] != entry.scope:
            entry.shadowed_by = winner[entry.agent_type]
    return entries


def load_agent_defs(cwd: str, all_tools: list) -> list[AgentDefinition]:
    return [e.defn for e in discover(cwd, all_tools=all_tools)
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
    lines += ['---', '', defn.system_prompt.strip(), '']
    return '\n'.join(lines)


def write_agent(defn: AgentDefinition, scope: str, cwd: str,
                all_tools: list, overwrite: bool = False) -> Path:
    path = agents_dir(scope, cwd) / f'{defn.agent_type}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open('w' if overwrite else 'x', encoding='utf-8')
    except FileExistsError:
        raise FileExistsError(f'Agent file already exists: {path}') from None
    with handle:
        handle.write(render_agent_md(defn, all_tools))
    return path


def remove_agent(entry: AgentEntry) -> None:
    if entry.scope == BUILT_IN or entry.path is None:
        raise ValueError('built-in agents cannot be removed')
    entry.path.unlink(missing_ok=True)


def validate_type(name: str) -> str | None:
    """The error that blocks the identifier step, or None."""
    if not name:
        return 'Agent type is required'
    if not _NAME.match(name):
        return ('Agent type must start and end with a letter or digit and '
                'contain only letters, digits and hyphens')
    if len(name) < _NAME_RANGE[0]:
        return f'Agent type must be at least {_NAME_RANGE[0]} characters long'
    if len(name) > _NAME_RANGE[1]:
        return f'Agent type must be less than {_NAME_RANGE[1]} characters'
    return None


def relative_path(entry: AgentEntry) -> str:
    if entry.path is None:
        return SCOPE_LABELS[BUILT_IN]
    return f'{_DISPLAY_DIRS[entry.scope]}/{entry.path.name}'


def validate(defn: AgentDefinition, known_tools: list[str],
             taken: list[tuple]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a draft definition. Neither blocks a save."""
    errors: list[str] = []
    warnings: list[str] = []
    name_error = validate_type(defn.agent_type)
    if name_error:
        errors.append(name_error)
    for other, scope in taken:
        if other == defn.agent_type:
            errors.append(f'Agent type "{defn.agent_type}" already exists '
                          f'in {SCOPE_LABELS[scope]}')
    if not defn.description.strip():
        errors.append('Description is required: it tells when to use this agent')
    elif not _DESCRIPTION_RANGE[0] <= len(defn.description) \
            <= _DESCRIPTION_RANGE[1]:
        warnings.append(f'Description is {len(defn.description)} characters; '
                        f'{_DESCRIPTION_RANGE[0]}-{_DESCRIPTION_RANGE[1]} '
                        f'reads better')
    names = [t.name for t in defn.tools]
    if not names:
        errors.append('No tools selected: this agent could not do anything')
    unknown = [n for n in names if n not in known_tools]
    if unknown:
        errors.append('Invalid tools: ' + ', '.join(unknown))
    prompt = defn.system_prompt.strip()
    if not prompt:
        errors.append('System prompt is required')
    elif len(prompt) < _PROMPT_MIN:
        errors.append(f'System prompt is too short '
                      f'({len(prompt)} characters)')
    elif len(prompt) > _PROMPT_MAX:
        warnings.append(f'System prompt is {len(prompt)} characters; it is '
                        'sent on every run')
    return errors, warnings


def _statusline_prompt() -> str:
    from pyclaw.config import __config_file__
    home = __config_file__.parent
    return STATUSLINE_SYSTEM_PROMPT.format(
        config_file=__config_file__,
        script=home / 'statusline-command.sh',
    )


def builtin_agent_defs(all_tools: list) -> list[AgentDefinition]:
    by_name = {t.name: t for t in all_tools}
    return [AgentDefinition(
        'statusline-setup',
        system_prompt=_statusline_prompt(),
        tools=[by_name[n] for n in ('Read', 'Edit') if n in by_name],
        description="Use this agent to configure the user's PyClaw status line "
                    "setting.")]


STATUSLINE_SYSTEM_PROMPT = '''You are a status line setup agent for PyClaw. Your \
job is to create or update the statusLine command in PyClaw's config at \
{config_file}.

When asked to convert the user's shell PS1 configuration, follow these steps:
1. Read the user's shell configuration files in this order of preference: \
~/.zshrc, ~/.bashrc, ~/.bash_profile, ~/.profile.
2. Extract the PS1 value with this regex: \
(?:^|\\n)\\s*(?:export\\s+)?PS1\\s*=\\s*["']([^"']+)["']
3. Convert PS1 escape sequences to shell commands:
   \\u -> $(whoami)     \\h -> $(hostname -s)   \\H -> $(hostname)
   \\w -> $(pwd)        \\W -> $(basename "$(pwd)")
   \\t -> $(date +%H:%M:%S)      \\d -> $(date "+%a %b %d")
   \\@ -> $(date +%I:%M%p)       \\$ -> $      \\n -> \\n      \\# -> #      \\! -> !
4. When using ANSI color codes use printf, and keep the colours. The status \
line is printed in a terminal using dimmed colors.
5. If the imported PS1 ends with a trailing "$" or ">" character, remove it.
6. If no PS1 is found and the user gave no other instructions, ask for further \
instructions.

How to use the statusLine command:
1. The command receives this JSON on stdin:
   {{
     "session_id": "string",       // Unique session ID
     "transcript_path": "string",  // Path to the conversation transcript
     "cwd": "string",              // Current working directory
     "permission_mode": "string",  // default | acceptEdits | plan | bypassPermissions
     "model": {{
       "id": "string",             // Model ID
       "display_name": "string"    // Model name shown in the UI
     }},
     "workspace": {{
       "current_dir": "string",    // Current working directory path
       "project_dir": "string",    // Project root directory path
       "added_dirs": ["string"]
     }},
     "version": "string",          // PyClaw version
     "usage": {{                   // Cumulative session token usage
       "prompt_tokens": number,
       "completion_tokens": number,
       "total_tokens": number,
       "cached_tokens": number
     }},
     "context_window": {{
       "total_input_tokens": number,
       "total_output_tokens": number,
       "context_window_size": number,      // Occupancy that triggers compaction
       "current_usage": {{"input_tokens": number}},
       "used_percentage": number,          // 0-100
       "remaining_percentage": number      // 0-100
     }}
   }}

   Read fields with jq, e.g.:
   - input=$(cat); echo "$(echo "$input" | jq -r '.model.display_name') in \
$(echo "$input" | jq -r '.workspace.current_dir')"
   - input=$(cat); remaining=$(echo "$input" | jq -r \
'.context_window.remaining_percentage // empty'); [ -n "$remaining" ] && echo \
"Context: $remaining% remaining"

2. For longer commands save {script} and reference that file from the config.
3. Update the config with:
   {{
     "statusLine": {{
       "type": "command",
       "command": "your_command_here"
     }}
   }}
   PyClaw re-reads its config before every status line refresh, so the row \
appears as soon as the file is saved.

Guidelines:
- Preserve every other setting in the config when updating it.
- If the config file is a symlink, update the file it points to.
- Return a summary of what was configured, including the script file name if \
one was used.
- If the script runs git commands, make them skip optional locks.
- At the end of your response, tell the parent agent that the "statusline-setup" \
agent must be used for further status line changes, and tell the user they can \
ask to keep tuning the status line.
'''
