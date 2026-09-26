"""/debug is a skill that ships with the program: the user asks for it by name,
the model may not, and its text is built from what this session recorded."""
import asyncio
import os

from pyclaw import config
from pyclaw.session import Session
from pyclaw.session import store as session_store
from pyclaw.slash import handle_slash
from pyclaw.team.builder import build_team


def _opened(tmp_path, monkeypatch, conversation='conv-debug', line=None):
    """Build the team inside a loop, as the terminal does, and hand back the
    session with whatever one slash line returned."""

    async def main():
        monkeypatch.setenv('PYCLAW_DEBUG', '')
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path),
                          conversation_id=conversation)
        session = Session(team, session_id=conversation)
        reply = await handle_slash(line, session) if line else None
        return session, team, reply

    return asyncio.run(main())


def _recorded(team, lines) -> None:
    path = session_store.conversation_log(team.lead_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_the_debug_skill_is_registered_on_every_team(tmp_path, monkeypatch):
    _session, team, _reply = _opened(tmp_path, monkeypatch)
    skill = team.skills.get('debug')
    assert skill.source == 'builtin'
    assert skill.disable_model_invocation is True
    assert skill.user_invocable is True
    assert skill.argument_hint == '[what went wrong]'
    assert tuple(skill.allowed_tools) == ('Read', 'Grep', 'Glob')


def test_the_model_is_never_shown_the_debug_skill(tmp_path, monkeypatch):
    from chatchat.knowledge.skills import Skill

    _session, team, _reply = _opened(tmp_path, monkeypatch)
    team.skills.register(Skill(name='commit', description='write a message',
                               body_text='summarise the change'))
    schemas = team.tool_schemas(team.tool_context)
    listed = next(schema['description'] for schema in schemas
                  if schema['name'] == 'Skill')
    offered = {line[2:].split(':')[0] for line in listed.splitlines()
               if line.startswith('- ')}
    assert 'commit' in offered
    assert 'debug' not in offered
    assert 'debug' in [skill.name for skill in team.skills.all()]
    assert 'debug' not in [skill.name for skill in team.skills.for_model()]


def test_asking_for_debug_turns_logging_on_and_sends_the_log_to_the_model(
        tmp_path, monkeypatch):
    import logging

    session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-asking')
    _recorded(team, ['INFO the run began', 'ERROR the tool failed'])
    assert not config.debug_on()
    note, prompt = asyncio.run(
        handle_slash('/debug the tool failed', session))

    assert config.debug_on() is True
    assert os.environ.get('PYCLAW_DEBUG') == '1'
    assert all(handler.level == logging.DEBUG
               for handler in logging.getLogger().handlers)
    assert prompt and note == 'Running /debug…'
    assert str(session_store.conversation_log('conv-asking')) in prompt
    assert 'ERROR the tool failed' in prompt
    assert '## The problem' in prompt and 'the tool failed' in prompt
    assert '## Settings' in prompt
    assert '## What to do' in prompt
    assert 'Logging started here' in prompt


def test_the_tail_of_a_long_log_is_bounded(tmp_path, monkeypatch):
    session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-long')
    _recorded(team, [f'line {number}' for number in range(1, 41)])
    _note, prompt = asyncio.run(handle_slash('/debug', session))

    assert 'line 40' in prompt and 'line 21' in prompt
    assert 'line 20' not in prompt and 'line 1' not in prompt
    assert 'bytes' in prompt


def test_nothing_is_claimed_about_logging_when_it_was_already_on(
        tmp_path, monkeypatch):
    session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-already')
    _recorded(team, ['INFO steady as she goes'])
    monkeypatch.setenv('PYCLAW_DEBUG', '1')
    _note, prompt = asyncio.run(handle_slash('/debug', session))

    assert 'Logging started here' not in prompt
    assert 'steady as she goes' in prompt


def test_skills_lists_the_built_in_one_with_its_own_terms(tmp_path, monkeypatch):
    session, _team, _reply = _opened(tmp_path, monkeypatch, 'conv-list')
    skills = asyncio.run(handle_slash('/skills', session))
    assert 'debug (builtin)' in skills
    assert 'only when you ask for it' in skills
    assert '/debug [what went wrong]' in skills


def test_a_skill_that_declares_tools_gets_them_for_the_rest_of_the_session(
        tmp_path, monkeypatch):
    from chatchat.knowledge.skills import Skill
    from chatchat.tools import tools as team_tools

    _session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-grants')
    team.skills.register(Skill(name='changelog', description='write notes',
                               body_text='summarise the commits',
                               allowed_tools=('Bash(git log:*)',)))
    gate = team._pyclaw_gate
    assert gate.decide('Bash', {'command': 'git log -3'}) == 'allow'
    assert gate.decide('Bash', {'command': 'git rebase -i HEAD~3'}) == 'ask'
    asyncio.run(team_tools.use_skill(team, team.lead, {'skill': 'changelog'}))
    assert ('allow', 'Bash(git log:*)', 'session') in gate.rule_listing()
    assert gate.decide('Bash', {'command': 'git rebase -i HEAD~3'}) == 'ask'


def test_setting_up_the_status_line_lets_its_own_reads_through(tmp_path,
                                                               monkeypatch):
    import os

    session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-statusline')
    gate = team._pyclaw_gate
    config = os.path.expanduser('~/.pyclaw/config.json')
    assert gate.decide('Read', {'file_path': os.path.expanduser('~/.zshrc')}) == 'ask'
    asyncio.run(handle_slash('/statusline make it show the branch', session))
    assert gate.decide('Read', {'file_path': os.path.expanduser('~/.zshrc')}) == 'allow'
    assert gate.decide('Edit', {'file_path': config}) == 'allow'
    assert gate.decide('Edit', {'file_path': os.path.expanduser('~/.bashrc')}) == 'ask'


def _in_repo(tmp_path, files=None):
    """A real git repository with a change staged but not committed."""
    import subprocess

    root = tmp_path / 'repo'
    root.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 't@example.com'],
                 ['config', 'user.name', 'test']):
        subprocess.run(['git', *args], cwd=root, check=True)
    (root / 'a.py').write_text('print(1)\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'a.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-qm', 'first'], cwd=root, check=True)
    for name, body in (files or {}).items():
        (root / name).write_text(body, encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=root, check=True)
    return root


def test_the_self_check_skills_are_registered_with_their_own_terms(
        tmp_path, monkeypatch):
    _session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-repo')
    by_name = {skill.name: skill for skill in team.skills.all()}
    assert {'doctor', 'review', 'security-review',
            'commit-push-pr'} <= set(by_name)
    assert by_name['review'].argument_hint.startswith('[pull request')
    assert 'Bash(gh pr diff:*)' in by_name['review'].allowed_tools
    assert by_name['doctor'].disable_model_invocation is True
    assert by_name['commit-push-pr'].disable_model_invocation is True
    assert by_name['security-review'].disable_model_invocation is False


def test_a_skill_the_user_keeps_for_themselves_is_not_offered_to_the_model(
        tmp_path, monkeypatch):
    _session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-hidden')
    listed = team.skills.listing(8_000)
    offered = {line[2:].split(':')[0] for line in listed.splitlines()
               if line.startswith('- ')}
    assert {'review', 'security-review'} <= offered
    assert not {'doctor', 'commit-push-pr', 'debug'} & offered


def test_typing_a_skill_name_runs_it_and_sends_its_prompt(tmp_path, monkeypatch):
    root = _in_repo(tmp_path, {'b.py': 'import os\n'})
    monkeypatch.setenv('PYCLAW_DEBUG', '')
    from pyclaw.team.builder import build_team

    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(root),
                          conversation_id='conv-typed')
        session = Session(team, session_id='conv-typed')
        return await handle_slash('/review the branch', session)

    note, prompt = asyncio.run(main())
    assert note == 'Running /review…'
    assert 'the branch' in prompt
    assert 'b.py' in prompt
    assert '## What to look at' in prompt


def test_the_security_review_reports_the_change_it_cannot_fit(tmp_path,
                                                              monkeypatch):
    root = _in_repo(tmp_path,
                   {'big.py': 'x = 1\n' * 6_000})
    monkeypatch.setenv('PYCLAW_DEBUG', '')
    from pyclaw.team.builder import build_team

    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(root),
                          conversation_id='conv-sec')
        session = Session(team, session_id='conv-sec')
        return await handle_slash('/security-review', session)

    _note, prompt = asyncio.run(main())
    assert 'clipped' in prompt
    assert 'big.py' in prompt
    assert '## What to look for' in prompt
    assert '## Reporting' in prompt


def test_the_doctor_names_the_files_it_checked(tmp_path, monkeypatch):
    _session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-doc')

    async def main():
        session = _session
        return await handle_slash('/doctor the model looks wrong', session)

    note, prompt = asyncio.run(main())
    assert 'Running /doctor…' == note
    assert 'Provider:' in prompt and 'agnes' in prompt
    assert str(config.__config_file__) in prompt
    assert 'the model looks wrong' in prompt


def test_a_skill_shows_up_where_the_terminal_lists_its_commands(tmp_path,
                                                                 monkeypatch):
    from pyclaw.slash import skill_rows, suggest

    session, team, _reply = _opened(tmp_path, monkeypatch, 'conv-palette')
    rows = skill_rows(session)
    names = [row['name'] for row in rows]
    assert 'review' in names and 'commit-push-pr' in names
    typed = [item['name'] for item in suggest('/secu', rows)]
    assert 'security-review' in typed
    assert {'review', 'security-review'} <= {item['name']
                                             for item in suggest('/', rows)}
