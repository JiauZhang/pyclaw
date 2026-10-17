"""The --allow-all flag, aligned with the reference.

The flag is process-level: launching any host (tui, serve, api, -p) with it
unlocks allowAll for the whole process, so IM and web sessions
started by that gateway inherit it. Without the flag, allow-all can neither be
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
    for argv in (['--allow-all'],
                 ['serve', '--allow-all'],
                 ['api', '--allow-all'],
                 ['tui', '--allow-all']):
        assert parser.parse_args(argv).allow_all is True


def test_the_flag_unlocks_allow_all_as_the_startup_mode(monkeypatch):
    args = _finalize(['--allow-all'], monkeypatch=monkeypatch)
    assert args.permission_mode == 'allowAll'
    assert args.allow_all_unlocked is True


def test_an_explicit_mode_wins_over_the_flag_but_the_unlock_persists(monkeypatch):
    args = _finalize(['--allow-all',
                      '--permission-mode', 'plan'], monkeypatch=monkeypatch)
    assert args.permission_mode == 'plan'
    assert args.allow_all_unlocked is True


def test_allow_all_without_the_flag_is_refused_at_startup(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exit_info:
        _finalize(['--permission-mode', 'allowAll'],
                  monkeypatch=monkeypatch)
    assert exit_info.value.code == 1
    assert '--allow-all' in capsys.readouterr().out


def test_a_legacy_bypass_mode_in_settings_is_refused_without_the_flag(
        monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _finalize([], config={'permissions': {'defaultMode': 'allowAll'}},
                  monkeypatch=monkeypatch)
    assert '--allow-all' in capsys.readouterr().out


def test_the_flag_is_refused_under_root(monkeypatch, capsys):
    monkeypatch.setattr(main.os, 'geteuid', lambda: 0)
    with pytest.raises(SystemExit) as exit_info:
        main._refuse_allow_all_under_root(
            main._build_parser().parse_args(['--allow-all']))
    assert exit_info.value.code == 1
    assert 'root/sudo' in capsys.readouterr().out


def test_the_flag_is_fine_for_normal_users(monkeypatch):
    monkeypatch.setattr(main.os, 'geteuid', lambda: 501)
    args = main._build_parser().parse_args(['--allow-all'])
    main._refuse_allow_all_under_root(args)


def test_a_gate_launched_in_allow_all_is_unlocked(tmp_path):
    gate = PermissionController(mode='allowAll', cwd=tmp_path)
    assert gate.allow_all_unlocked
    assert gate.allow_all_available
    assert gate.decide('Bash', {'command': 'curl example.com'}) == 'allow'


def test_deny_rules_still_win_under_allow_all(tmp_path):
    gate = PermissionController(mode='allowAll', cwd=tmp_path,
                                deny=['Bash(rm -rf:*)'])
    assert gate.decide('Bash', {'command': 'rm -rf /'}) == 'deny'
    assert gate.decide('Bash', {'command': 'ls'}) == 'allow'


def test_switching_into_allow_all_needs_the_unlock(tmp_path):
    gate = PermissionController(mode='default', cwd=tmp_path)
    with pytest.raises(ValueError) as exc_info:
        gate.check_switch('allowAll')
    assert '--allow-all' in str(exc_info.value)

    unlocked = PermissionController(mode='plan', cwd=tmp_path,
                                    allow_all_unlocked=True)
    unlocked.check_switch('allowAll')


def test_the_gateway_threads_the_mode_into_every_new_session(monkeypatch):
    from pyclaw.gateway import server as gateway_server
    seen = {}
    monkeypatch.setattr(gateway_server, 'build_team',
                        lambda **kwargs: seen.setdefault('team', kwargs))
    monkeypatch.setattr(gateway_server, 'Session',
                        lambda team, session_id=None: seen)
    server = gateway_server.GatewayServer(
        gateway_server.GatewayConfig(permission_mode='allowAll',
                                     allow_all_unlocked=True))
    server._new_session('conv-1')
    assert seen['team']['permission_mode'] == 'allowAll'
    assert seen['team']['allow_all_unlocked'] is True


def test_the_flag_beats_a_default_mode_from_settings(monkeypatch):
    args = _finalize(['--allow-all'],
                     config={'permissions': {'defaultMode': 'default'}},
                     monkeypatch=monkeypatch)
    assert args.permission_mode == 'allowAll'
    assert args.allow_all_unlocked is True
