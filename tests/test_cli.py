from unittest import mock

import pytest

from pyclaw import cli


def _run(capsys, argv, monkeypatch):
    with mock.patch("pyclaw.cli.subprocess.run") as run, mock.patch(
        "pyclaw.config.load", return_value={"gateway": {"http": {"port": 12321, "host": "127.0.0.1"}}}
    ):
        from pyclaw.__main__ import main

        monkeypatch.setattr("sys.argv", ["pyclaw", *argv])
        main()
        return run, capsys.readouterr()


def test_find_listener_pids_parses_output():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n34100\n"
        assert cli.find_listener_pids(12321) == [34004, 34100]


def test_find_listener_pids_empty():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = ""
        assert cli.find_listener_pids(12321) == []


def test_find_listener_pids_lsof_missing():
    with mock.patch("pyclaw.cli.subprocess.run", side_effect=FileNotFoundError):
        assert cli.find_listener_pids(12321) == []


def test_find_serve_pids_parses_output():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n"
        assert cli.find_serve_pids() == [34004]


def test_find_serve_pids_empty():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = ""
        assert cli.find_serve_pids() == []


def test_kill_pids_uses_sigterm_by_default():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        killed = cli.kill_pids([34004])
        assert killed == [34004]
        run.assert_called_once_with(["kill", "-15", "34004"], check=False)


def test_kill_pids_force_uses_sigkill():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        killed = cli.kill_pids([34004], force=True)
        assert killed == [34004]
        run.assert_called_once_with(["kill", "-9", "34004"], check=False)


def test_kill_pids_skips_on_missing_kill():
    with mock.patch("pyclaw.cli.subprocess.run", side_effect=FileNotFoundError):
        assert cli.kill_pids([34004]) == []


def test_stop_server_by_port():
    with mock.patch("pyclaw.cli.subprocess.run") as run, mock.patch(
        "pyclaw.config.load", return_value={"gateway": {"http": {"port": 12321}}}
    ):
        run.return_value.stdout = "34004\n"
        killed = cli.stop_server(port=12321)
        assert killed == [34004]
        run.assert_any_call(["lsof", "-ti", "tcp:12321"], capture_output=True, text=True, check=False)
        run.assert_any_call(["kill", "-15", "34004"], check=False)


def test_stop_server_default_port_from_config():
    with mock.patch("pyclaw.cli.subprocess.run") as run, mock.patch(
        "pyclaw.config.load", return_value={"gateway": {"http": {"port": 5000}}}
    ):
        run.return_value.stdout = "9\n"
        killed = cli.stop_server()
        assert killed == [9]
        run.assert_any_call(["lsof", "-ti", "tcp:5000"], capture_output=True, text=True, check=False)


def test_stop_server_no_listener_returns_empty():
    with mock.patch("pyclaw.cli.subprocess.run") as run, mock.patch(
        "pyclaw.config.load", return_value={"gateway": {"http": {"port": 12321}}}
    ):
        run.return_value.stdout = ""
        assert cli.stop_server() == []


def test_stop_server_all_kills_every_serve_process():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n34100\n"
        killed = cli.stop_server(all_processes=True)
        assert killed == [34004, 34100]
        run.assert_any_call(["pgrep", "-f", "pyclaw serve"], capture_output=True, text=True, check=False)


def test_stop_command_no_process_reports(capsys, monkeypatch):
    run, out = _run(capsys, ["stop"], monkeypatch)
    assert "No running PyClaw gateway" in out.out
    run.assert_any_call(["lsof", "-ti", "tcp:12321"], capture_output=True, text=True, check=False)


def test_stop_command_kills_and_reports(capsys, monkeypatch):
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n"
        with mock.patch("pyclaw.config.load", return_value={"gateway": {"http": {"port": 12321}}}):
            from pyclaw.__main__ import main

            monkeypatch.setattr("sys.argv", ["pyclaw", "stop"])
            main()
    out = capsys.readouterr()
    assert "Stopped" in out.out
    assert "34004" in out.out


def test_stop_command_force_uses_sigkill(capsys, monkeypatch):
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n"
        with mock.patch("pyclaw.config.load", return_value={"gateway": {"http": {"port": 12321}}}):
            from pyclaw.__main__ import main

            monkeypatch.setattr("sys.argv", ["pyclaw", "stop", "--force"])
            main()
    run.assert_any_call(["kill", "-9", "34004"], check=False)


def test_stop_command_all_kills_serve_processes(capsys, monkeypatch):
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n"
        from pyclaw.__main__ import main

        monkeypatch.setattr("sys.argv", ["pyclaw", "stop", "--all"])
        main()
    run.assert_any_call(["pgrep", "-f", "pyclaw serve"], capture_output=True, text=True, check=False)
