from unittest import mock

from pyclaw import cli


def _main(capsys, argv, monkeypatch, stdout=""):
    with mock.patch("pyclaw.cli.subprocess.run") as run, mock.patch(
        "pyclaw.config.load", return_value={"gateway": {"http": {"port": 12321, "host": "127.0.0.1"}}}
    ):
        from pyclaw.__main__ import main

        run.return_value.stdout = stdout
        monkeypatch.setattr("sys.argv", ["pyclaw", *argv])
        main()
        return run, capsys.readouterr()


def test_pid_lookup_parses_one_pid_per_line():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n34100\n"
        assert cli.find_listener_pids(12321) == [34004, 34100]
        run.return_value.stdout = "34004\n"
        assert cli.find_serve_pids() == [34004]


def test_pid_lookup_returns_empty_on_blank_output():
    for finder, args in ((cli.find_listener_pids, (12321,)),
                         (cli.find_serve_pids, ())):
        with mock.patch("pyclaw.cli.subprocess.run") as run:
            run.return_value.stdout = ""
            assert finder(*args) == []


def test_find_listener_pids_lsof_missing():
    with mock.patch("pyclaw.cli.subprocess.run", side_effect=FileNotFoundError):
        assert cli.find_listener_pids(12321) == []


def test_kill_pids_sends_the_requested_signal():
    for force, signal in ((False, "-15"), (True, "-9")):
        with mock.patch("pyclaw.cli.subprocess.run") as run:
            assert cli.kill_pids([34004], force=force) == [34004]
            run.assert_called_once_with(["kill", signal, "34004"], check=False)


def test_kill_pids_skips_on_missing_kill():
    with mock.patch("pyclaw.cli.subprocess.run", side_effect=FileNotFoundError):
        assert cli.kill_pids([34004]) == []


def test_stop_server_resolves_the_port_from_the_argument_or_the_config():
    cases = (
        ({"port": 12321}, 5000, "34004\n", [34004], "tcp:12321"),
        ({}, 5000, "9\n", [9], "tcp:5000"),
        ({}, 12321, "", [], "tcp:12321"),
    )
    for kwargs, configured, stdout, killed, target in cases:
        with mock.patch("pyclaw.cli.subprocess.run") as run, mock.patch(
            "pyclaw.config.load", return_value={"gateway": {"http": {"port": configured}}}
        ):
            run.return_value.stdout = stdout
            assert cli.stop_server(**kwargs) == killed
            run.assert_any_call(["lsof", "-ti", target], capture_output=True,
                                text=True, check=False)


def test_stop_server_all_kills_every_serve_process():
    with mock.patch("pyclaw.cli.subprocess.run") as run:
        run.return_value.stdout = "34004\n34100\n"
        killed = cli.stop_server(all_processes=True)
        assert killed == [34004, 34100]
        run.assert_any_call(["pgrep", "-f", "pyclaw serve"], capture_output=True, text=True, check=False)


def test_stop_command_no_process_reports(capsys, monkeypatch):
    run, out = _main(capsys, ["stop"], monkeypatch)
    assert "No running PyClaw gateway" in out.out
    run.assert_any_call(["lsof", "-ti", "tcp:12321"], capture_output=True, text=True, check=False)


def test_stop_command_kills_and_reports(capsys, monkeypatch):
    run, out = _main(capsys, ["stop"], monkeypatch, stdout="34004\n")
    assert "Stopped" in out.out
    assert "34004" in out.out
    run.assert_any_call(["kill", "-15", "34004"], check=False)


def test_stop_command_force_uses_sigkill(capsys, monkeypatch):
    run, out = _main(capsys, ["stop", "--force"], monkeypatch, stdout="34004\n")
    run.assert_any_call(["kill", "-9", "34004"], check=False)


def test_stop_command_all_kills_serve_processes(capsys, monkeypatch):
    run, out = _main(capsys, ["stop", "--all"], monkeypatch, stdout="34004\n")
    run.assert_any_call(["pgrep", "-f", "pyclaw serve"], capture_output=True,
                        text=True, check=False)
    assert "34004" in out.out
