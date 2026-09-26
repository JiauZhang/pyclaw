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
    assert prompt and 'recorded' in note
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
