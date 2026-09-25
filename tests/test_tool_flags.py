"""The command line can say which tools exist and how they are allowed, so a
run can be narrowed without editing settings files."""
import asyncio

from pyclaw import __main__
from pyclaw.team.builder import build_team
from pyclaw.permissions import split_rules


def test_one_value_can_carry_several_comma_separated_rules():
    args = __main__._build_parser().parse_args(
        ['-p', 'hi', '--allowed-tools', 'Bash(git push:*),Edit',
         '--disallowed-tools', 'Bash(curl:*)', '--tools', 'Read,Grep'])
    assert args.allowed_tools == ['Bash(git push:*)', 'Edit']
    assert args.disallowed_tools == ['Bash(curl:*)']
    assert args.tools == ['Read', 'Grep']


def test_a_repeated_flag_adds_to_the_list():
    args = __main__._build_parser().parse_args(
        ['tui', '--allowed-tools', 'Read', '--allowed-tools', 'Edit,WebSearch'])
    assert args.allowed_tools == ['Read', 'Edit', 'WebSearch']


def test_a_rule_without_tools_is_an_empty_list_not_a_denial_of_nothing():
    args = __main__._build_parser().parse_args(['tui'])
    assert args.allowed_tools == []
    assert args.disallowed_tools == []
    assert args.tools is None


def test_a_comma_inside_a_rule_argument_stays_in_that_rule():
    assert split_rules('Bash(echo a,b),Read') == ['Bash(echo a,b)', 'Read']


def _team(tmp_path, **kw):
    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path), **kw)
        await team.end_session('done')
        return team._pyclaw_gate
    return asyncio.run(main())


def test_naming_the_tools_denies_every_built_in_left_out(tmp_path):
    gate = _team(tmp_path, base_tools=['Read', 'Glob'])
    assert gate.allowed_tool('Read')
    assert not gate.allowed_tool('Bash')
    assert not gate.allowed_tool('Edit')


def test_an_empty_tool_list_leaves_the_model_no_tools_at_all(tmp_path):
    gate = _team(tmp_path, base_tools=[])
    assert not gate.allowed_tool('Read')


def test_the_named_tools_are_a_denial_rule_of_their_own(tmp_path):
    gate = _team(tmp_path, base_tools=['Read'])
    assert ('deny', 'Bash', 'cli') in gate.rule_listing()


def test_the_allowed_and_disallowed_lists_become_permission_rules(tmp_path):
    gate = _team(tmp_path, allowed_tools=['Bash(git push:*)'],
                 disallowed_tools=['Edit'])
    assert ('allow', 'Bash(git push:*)', 'cli') in gate.rule_listing()
    assert ('deny', 'Edit', 'cli') in gate.rule_listing()
    assert not gate.allowed_tool('Edit')


def test_a_denied_tool_wins_over_an_allow_rule_the_same_run(tmp_path):
    gate = _team(tmp_path, allowed_tools=['Bash'], disallowed_tools=['Bash'])
    assert not gate.allowed_tool('Bash')
