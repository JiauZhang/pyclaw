"""The self-check skills: they read the repository and the configuration as
they stand right now, and hand the model what it needs to report on them."""
from __future__ import annotations

import platform
import shutil
import subprocess

from chatchat.knowledge.skills import Skill

from pyclaw import config
from pyclaw.home import pyclaw_home
from pyclaw.session import store as session_store
from pyclaw.tools.names import AGENT, BASH, GLOB, GREP, READ
from pyclaw.version import __version__

DIFF_LIMIT = 24_000
LIST_LIMIT = 4_000

GIT_RULES = ('Bash(git status:*)', 'Bash(git diff:*)', 'Bash(git log:*)',
             'Bash(git show:*)', 'Bash(git branch:*)', 'Bash(git remote:*)')
GH_RULES = ('Bash(gh pr view:*)', 'Bash(gh pr list:*)', 'Bash(gh pr diff:*)')


def _git(cwd, *args) -> str:
    try:
        done = subprocess.run(('git', *args), cwd=str(cwd),
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout.strip() if done.returncode == 0 else ''


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f'\n\n… clipped, {len(text) - limit} characters left ' \
                          f'out. Read the file or run the command yourself ' \
                          f'for the rest.'


def _state(cwd) -> dict:
    default = _git(cwd, 'rev-parse', '--abbrev-ref', 'origin/HEAD') or 'HEAD'
    base = default.split('/', 1)[-1] if default != 'HEAD' else ''
    ahead = _git(cwd, 'rev-list', '--count', f'{base}..HEAD') if base else '0'
    return {
        'branch': _git(cwd, 'branch', '--show-current') or '(detached)',
        'default': base or 'HEAD',
        'ahead': ahead or '0',
        'status': _git(cwd, 'status', '--porcelain=v1', '--branch'),
        'commits': _git(cwd, 'log', '--no-decorate', '--oneline',
                        f'{base}..HEAD' if base else '-10'),
        'files': '\n'.join(filter(None, (
            _git(cwd, 'diff', '--name-only', 'HEAD'),
            _git(cwd, 'diff', '--name-only', f'{base}...HEAD') if base else ''))),
        'diff': '\n'.join(filter(None, (
            _git(cwd, 'diff', 'HEAD'),
            _git(cwd, 'diff', f'{base}...HEAD') if base else ''))),
        'has_gh': shutil.which('gh') is not None,
    }


def _fence(label: str, text: str) -> str:
    return f'### {label}\n\n```\n{text or "(nothing)"}\n```' if text.strip() \
        else f'### {label}\n\n(empty)'


def _review_prompt(team, args: str) -> str:
    cwd = team.tool_context.cwd
    state = _state(cwd)
    asking = args.strip() or 'the branch you are on now'
    gh = ('`gh` is on PATH, so a pull request can be read with '
          '`gh pr list`, `gh pr view <number>`, `gh pr diff <number>`.'
          if state['has_gh'] else
          '`gh` is not installed here, so review the commits and the diff in '
          'front of you instead of fetching a pull request.')
    return '\n'.join([
        'Review code and say what you found. Be specific: name the file and '
        'the line, and say what breaks or what you would change.',
        '',
        f'## What to look at\n\n{asking}',
        '',
        '## The repository now',
        '',
        _fence('Branch', state['branch']),
        _fence('Changed against the base branch', _clip(
            state['files'], LIST_LIMIT)),
        _fence('Diff', _clip(state['diff'], DIFF_LIMIT)),
        '',
        '## How to go about it',
        '',
        f'1. {gh}',
        '2. Read the surrounding code before judging a line on its own.',
        '3. Cover correctness, conventions the repository already follows, '
        'missing tests, and anything that costs more than it needs to.',
        '4. Separate what must change from what would be nice, and say which '
        'is which.',
        '5. End with the two or three things you would do first.',
    ])


def _security_prompt(team, args: str) -> str:
    cwd = team.tool_context.cwd
    state = _state(cwd)
    return '\n'.join([
        'Security review of what this branch adds. Only report what the change '
        'itself introduces, not what was already there.',
        '',
        '## The change',
        '',
        _fence('Status', state['status']),
        _fence('Commits', state['commits']),
        _fence('Files', _clip(state['files'], LIST_LIMIT)),
        _fence('Diff', _clip(state['diff'], DIFF_LIMIT)),
        '',
        '## What to look for',
        '',
        '- Input reaching a shell, a SQL string, a path, a template or a '
        'deserialiser without being checked.',
        '- Secrets, tokens or keys in the diff, in a committed file, or in a '
        'log line.',
        '- Authentication and authorisation checks that can be skipped, and '
        'endpoints that took on a caller they did not have before.',
        '- Command execution, file writes and network calls built out of '
        'values someone supplied.',
        '- Cryptography done by hand: weak modes, fixed IVs, home-made '
        'comparison of secrets.',
        '',
        '## What to leave out',
        '',
        '- Service disruption, rate limiting and resource exhaustion.',
        '- Anything theoretical you are not sure is reachable: say the '
        'condition instead of claiming the flaw.',
        '- Style, naming and test coverage. Those belong to /review.',
        '',
        '## Reporting',
        '',
        'For each finding: how bad it is, where it is, how it would be '
        'reached, and the smallest change that closes it. Say up front if '
        'you found nothing you are confident about — an empty list is a good '
        'answer here.',
    ])


def _commit_prompt(team, args: str) -> str:
    cwd = team.tool_context.cwd
    state = _state(cwd)
    existing = _git(cwd, 'rev-parse', '--abbrev-ref', '@{upstream}')
    return '\n'.join([
        'Commit what is here, push it, and open or update the pull request.',
        '',
        '## The repository now',
        '',
        _fence('Branch', state['branch']),
        _fence('Status', state['status']),
        _fence('Commits not on the base branch', state['commits']),
        _fence('Diff', _clip(state['diff'], DIFF_LIMIT)),
        _fence('Upstream', existing),
        '',
        '## Rules for this run',
        '',
        '- Do not change git configuration.',
        '- Nothing destructive: no force push, no hard reset, no history '
        'rewrite, unless the user asks for it in their own words.',
        '- Never skip a hook, and never pass an interactive flag.',
        '- Look at what is staged before committing it. Files like `.env` or a '
        'key belong out of the commit; say so rather than committing them.',
        '- Every command that changes the repository is put to the user '
        'before it runs. Do not talk about pushing as if it were done.',
        '',
        '## Steps',
        '',
        f'1. If you are on `{state["default"]}`, make a branch for this work '
        f'first.',
        '2. Read every commit that will be in the pull request, not just the '
        'last one, and write one message that says what the change does.',
        '3. Commit it.',
        '4. Push the branch and set its upstream.',
        '5. If a pull request for this branch already exists, update its '
        'title and body to match the diff. Otherwise open one: a title under '
        '70 characters, then a body with a short summary and a test plan as a '
        'checklist.',
        '6. Give back the pull request address at the end.',
    ])


def _doctor_prompt(team, args: str) -> str:
    cwd = team.tool_context.cwd
    home = pyclaw_home()
    settings = team._pyclaw_gate.settings_files() if getattr(
        team, '_pyclaw_gate', None) else {}
    in_git = _git(cwd, 'rev-parse', '--is-inside-work-tree') == 'true'
    branch = _git(cwd, 'branch', '--show-current') or '?'
    rules = ', '.join(f'{name}={path}'
                      for name, path in settings.items()) or 'unknown'
    writable = 'yes' if _writable(home / 'logs') else 'NO'
    lines = [
        'Check the state of this installation and explain anything that looks '
        'wrong. Say what is fine in one line each; spend the words on what is '
        'not.',
        '',
        '## What it looks like now',
        '',
        f'- pyclaw {__version__} on Python {platform.python_version()}, '
        f'{platform.system()} {platform.machine()}',
        f'- Provider: {team.provider}  Model: {team.model}',
        f'- Config: {config.__config_file__} '
        f'({"read" if config.load() else "empty or unreadable"})',
        f'- Home: {home}  Logs writable: {writable}',
        f'- Permission settings: {rules}',
        f'- Hooks configured: {len(team.hooks.configured())}',
        f'- Skills read: {len(team.skills.all())}  '
        f'problems: {"; ".join(team.skills.problems) or "none"}',
        f'- Git repository: {in_git}  Branch: {branch}',
        f'- Worktree: {team.worktree["path"] if team.worktree else "none"}',
        f'- Log of this conversation: '
        f'{session_store.conversation_log(team.lead_session_id)}',
    ]
    if args.strip():
        lines += ['', f'## What the user asked about\n\n{args.strip()}']
    lines += [
        '',
        '## What to do',
        '',
        '1. Re-read anything that looks off by checking the file itself rather '
        'than trusting the line above.',
        '2. Name the setting or file to change, and what each choice means.',
        '3. Say what you cannot check from here.',
    ]
    return '\n'.join(lines)


def _writable(path) -> bool:
    probe = path / '.write-probe'
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text('', encoding='utf-8')
    except OSError:
        return False
    probe.unlink(missing_ok=True)
    return True


def register(registry, team) -> None:
    registry.register(Skill(
        name='doctor',
        description='Check how this pyclaw installation is configured and say '
                    'what looks wrong',
        allowed_tools=(READ, GREP, GLOB),
        argument_hint='[what concerns you]',
        disable_model_invocation=True,
        source='builtin',
        builder=lambda args: _doctor_prompt(team, args)))
    registry.register(Skill(
        name='review',
        description='Review the code on this branch, or a pull request by '
                    'number, and say what to change',
        allowed_tools=GIT_RULES + GH_RULES + (READ, GREP, GLOB),
        argument_hint='[pull request number or what to review]',
        source='builtin',
        builder=lambda args: _review_prompt(team, args)))
    registry.register(Skill(
        name='security-review',
        description='Look only for what this branch makes exploitable, and '
                    'report the findings with their severity',
        allowed_tools=GIT_RULES + (READ, GREP, GLOB, AGENT),
        argument_hint='[what to concentrate on]',
        source='builtin',
        builder=lambda args: _security_prompt(team, args)))
    registry.register(Skill(
        name='commit-push-pr',
        description='Commit the work here, push the branch, and open or update '
                    'the pull request',
        allowed_tools=GIT_RULES,
        disable_model_invocation=True,
        source='builtin',
        builder=lambda args: _commit_prompt(team, args)))
