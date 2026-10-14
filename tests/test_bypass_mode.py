"""The --dangerously-skip-permissions flag, aligned with the reference.

The flag is process-level: launching any host (tui, serve, api, -p) with it
unlocks bypassPermissions for the whole process, so IM and web sessions
started by that gateway inherit it. Without the flag, bypass can neither be
requested at startup nor switched into at runtime, and root/sudo is refused.
"""
import pytest

from pyclaw import __main__ as main
from pyclaw.permissions import PermissionController


def _finalize(argv, config=None, monkeypatch=None):
    monkeypatch.setattr(main, 'load_config', lambda: (config or {}))
    args = main._build_parser().parse_args(argv)
    return main._finalize_args(args)


def test_the_flag_is_accepted_by_every_host():
    parser = main._build_parser()
    for argv in (['--dangerously-skip-permissions'],
                 ['serve', '--dangerously-skip-permissions'],
                 ['api', '--dangerously-skip-permissions'],
                 ['tui', '--dangerously-skip-permissions']):
        assert parser.parse_args(argv).dangerously_skip_permissions is True


def test_the_flag_unlocks_bypass_as_the_startup_mode(monkeypatch):
    args = _finalize(['--dangerously-skip-permissions'], monkeypatch=monkeypatch)
    assert args.permission_mode == 'bypassPermissions'
    assert args.bypass_unlocked is True


def test_an_explicit_mode_wins_over_the_flag_but_the_unlock_persists(monkeypatch):
    args = _finalize(['--dangerously-skip-permissions',
                      '--permission-mode', 'plan'], monkeypatch=monkeypatch)
    assert args.permission_mode == 'plan'
    assert args.bypass_unlocked is True


def test_bypass_without_the_flag_is_refused_at_startup(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exit_info:
        _finalize(['--permission-mode', 'bypassPermissions'],
                  monkeypatch=monkeypatch)
    assert exit_info.value.code == 1
    assert '--dangerously-skip-permissions' in capsys.readouterr().out


def test_a_bypass_default_mode_in_settings_is_refused_without_the_flag(
        monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _finalize([], config={'permissions': {'defaultMode': 'bypassPermissions'}},
                  monkeypatch=monkeypatch)
    assert '--dangerously-skip-permissions' in capsys.readouterr().out


def test_the_flag_is_refused_under_root(monkeypatch, capsys):
    monkeypatch.setattr(main.os, 'geteuid', lambda: 0)
    with pytest.raises(SystemExit) as exit_info:
        main._refuse_bypass_under_root(
            main._build_parser().parse_args(['--dangerously-skip-permissions']))
    assert exit_info.value.code == 1
    assert 'root/sudo' in capsys.readouterr().out


def test_the_flag_is_fine_for_normal_users(monkeypatch):
    monkeypatch.setattr(main.os, 'geteuid', lambda: 501)
    args = main._build_parser().parse_args(['--dangerously-skip-permissions'])
    main._refuse_bypass_under_root(args)


def test_a_gate_launched_in_bypass_is_unlocked(tmp_path):
    gate = PermissionController(mode='bypassPermissions', cwd=tmp_path)
    assert gate.bypass_unlocked
    assert gate.bypass_available
    assert gate.decide('Bash', {'command': 'curl example.com'}) == 'allow'


def test_deny_rules_still_win_under_bypass(tmp_path):
    gate = PermissionController(mode='bypassPermissions', cwd=tmp_path,
                                deny=['Bash(rm -rf:*)'])
    assert gate.decide('Bash', {'command': 'rm -rf /'}) == 'deny'
    assert gate.decide('Bash', {'command': 'ls'}) == 'allow'


def test_switching_into_bypass_needs_the_unlock(tmp_path):
    gate = PermissionController(mode='default', cwd=tmp_path)
    with pytest.raises(ValueError) as exc_info:
        gate.check_switch('bypassPermissions')
    assert '--dangerously-skip-permissions' in str(exc_info.value)

    unlocked = PermissionController(mode='plan', cwd=tmp_path,
                                    bypass_unlocked=True)
    unlocked.check_switch('bypassPermissions')


def test_the_gateway_threads_the_mode_into_every_new_session(monkeypatch):
    from pyclaw.gateway import server as gateway_server
    seen = {}
    monkeypatch.setattr(gateway_server, 'build_team',
                        lambda **kwargs: seen.setdefault('team', kwargs))
    monkeypatch.setattr(gateway_server, 'Session',
                        lambda team, session_id=None: seen)
    server = gateway_server.GatewayServer(
        gateway_server.GatewayConfig(permission_mode='bypassPermissions',
                                     bypass_unlocked=True))
    server._new_session('conv-1')
    assert seen['team']['permission_mode'] == 'bypassPermissions'
    assert seen['team']['bypass_unlocked'] is True


def test_the_flag_beats_a_default_mode_from_settings(monkeypatch):
    args = _finalize(['--dangerously-skip-permissions'],
                     config={'permissions': {'defaultMode': 'default'}},
                     monkeypatch=monkeypatch)
    assert args.permission_mode == 'bypassPermissions'
    assert args.bypass_unlocked is True
