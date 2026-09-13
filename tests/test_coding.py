import asyncio
import re
import tempfile
from pathlib import Path

from conippets import json

from pyclaw.tools.coding import (PermissionController, build_coding_tools,
                                 next_mode, parse_mode)
from pyclaw.tools.coding.shell_rules import (bash_rule_matches,
                                             is_dangerous_removal,
                                             is_read_only, parse_bash_rule)


def _tools(d):
    return {t.name: t for t in build_coding_tools(d)}


def _read(p):
    return p.read_text(encoding="utf-8")


def test_read_and_find():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("alpha\nbeta\n")
        t = _tools(d)
        out = t["Read"](file_path="a.txt")
        assert "alpha" in out and "a.txt" in out
        g = t["Glob"](pattern="*.txt")
        assert "a.txt" in g
        ls = t["LS"](path=".")
        assert "a.txt" in ls


def test_grep_matches_and_limits():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("x1\n---\nx2\n")
        t = _tools(d)
        out = asyncio.run(t["Grep"](pattern="x[0-9]"))
        assert "a.txt:1: x1" in out and "a.txt:3: x2" in out


def test_path_escape_guard():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        for tool, kw in (("Read", {"file_path": "../secret"}),
                         ("Write", {"file_path": "../x", "content": "y"})):
            assert t[tool](**kw).startswith("Error")


def test_edit_unique_and_ambiguous():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("one\ntwo\none\n")
        t = _tools(d)
        ambiguous = t["Edit"](file_path="a.txt", old_string="one",
                              new_string="X")
        assert "not unique" in ambiguous
        ok = t["Edit"](file_path="a.txt", old_string="two", new_string="TWO")
        assert "Edited" in ok and "TWO" in _read(root / "a.txt")
        missing = t["Edit"](file_path="a.txt", old_string="zzz",
                            new_string="y")
        assert "not found" in missing


def test_write_and_multi_edit():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        t = _tools(d)
        assert "Wrote" in t["Write"](file_path="b.txt", content="hi\n")
        out = t["MultiEdit"](file_path="b.txt", edits=[
            {"old_string": "hi", "new_string": "hello"}])
        assert "Edited" in out and "hello" in _read(root / "b.txt")


def test_mode_parse_and_cycle():
    for m in ("default", "acceptEdits", "plan"):
        assert parse_mode(m).value == m
    assert next_mode("default").value == "acceptEdits"
    assert next_mode("acceptEdits").value == "plan"
    assert next_mode("plan").value == "default"


def test_decide_matrix():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d)
        assert g.decide("Read", {"file_path": "a.txt"}) == "allow"
        assert g.decide("Edit", {"file_path": "a.txt"}) == "ask"
        assert g.decide("Write", {"file_path": "../x"}) == "ask"
        assert g.decide("Read", {"file_path": "../x"}) == "ask"
        assert g.decide("datetime", {}) == "allow"

        plan = PermissionController(mode="plan", cwd=d)
        assert plan.decide("Edit", {"file_path": "a.txt"}) == "deny"
        assert plan.decide("Read", {"file_path": "../x"}) == "allow"

        ae = PermissionController(mode="acceptEdits", cwd=d)
        assert ae.decide("Edit", {"file_path": "a.txt"}) == "allow"


def test_deny_removes_tool_and_precedence():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, deny=["Edit"])
        assert g.allowed_tool("Edit") is False
        assert g.allowed_tool("Read") is True
        g2 = PermissionController(mode="default", cwd=d,
                                  deny=["Edit"], allow=["Edit"])
        assert g2.decide("Edit", {"file_path": "a.txt"}) == "deny"


def test_ask_flow_authorize():
    with tempfile.TemporaryDirectory() as d:

        async def approve(name, inp):
            return "approved"

        g = PermissionController(mode="default", cwd=d, request=approve)
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True

        async def denied(name, inp):
            return "denied"

        g2 = PermissionController(mode="default", cwd=d, request=denied)
        res = asyncio.run(g2.authorize("Edit", {"file_path": "a.txt"}))
        assert isinstance(res, dict) and res["decision"] == "block"

        g3 = PermissionController(mode="default", cwd=d)
        res3 = asyncio.run(g3.authorize("Edit", {"file_path": "a.txt"}))
        assert isinstance(res3, dict) and res3["decision"] == "block"


def test_bash_tool_runs_in_workspace():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "marker.txt").write_text("x\n")
        t = _tools(d)
        assert "Bash" in t
        assert "marker.txt" in t["Bash"](command="ls")
        out = t["Bash"](command="python3 -c \"import os;"
                                "print(os.path.realpath(os.getcwd()))\"")
        assert str(root.resolve()) in out


def test_bash_merges_stdout_and_stderr():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = t["Bash"](command="echo out; echo err 1>&2")
        assert "out" in out and "err" in out


def test_bash_reports_nonzero_exit_with_output():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = t["Bash"](command="echo boom 1>&2; exit 3")
        assert out.startswith("Exit code 3")
        assert "boom" in out


def test_bash_grep_exit_one_is_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("alpha\n")
        t = _tools(d)
        out = t["Bash"](command="grep zzz a.txt")
        assert "No matches found" in out
        assert "Exit code" not in out


def test_bash_test_exit_one_is_condition_false():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = t["Bash"](command="test 1 = 2")
        assert "Condition is false" in out
        assert "Exit code" not in out


def test_bash_timeout_kills_command():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = t["Bash"](command="sleep 5", timeout=200)
        assert "timed out" in out


def test_bash_truncates_large_output():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = t["Bash"](command="python3 -c \"print('y' * 40000)\"")
        assert "truncated" in out
        assert len(out) < 40000


def test_bash_rule_parse_and_match():
    assert parse_bash_rule("Bash(npm run test:*)") == ("prefix", "npm run test")
    assert parse_bash_rule("Bash(ls)") == ("exact", "ls")
    assert parse_bash_rule("Bash(git * logs)")[0] == "wildcard"

    assert bash_rule_matches("Bash(npm run test:*)", "npm run test")
    assert bash_rule_matches("Bash(npm run test:*)", "npm run test -- unit")
    assert not bash_rule_matches("Bash(npm run test:*)", "npm run test:unit")
    assert not bash_rule_matches("Bash(npm run test:*)", "npm runner")
    assert bash_rule_matches("Bash(git *)", "git add .")
    assert bash_rule_matches("Bash(git *)", "git")
    assert bash_rule_matches("Bash(ls)", "ls")
    assert not bash_rule_matches("Bash(ls)", "ls -la")
    assert bash_rule_matches("Bash(grep:*)", "xargs grep pattern")
    assert not bash_rule_matches("Bash(grep:*)", "xargs -n1 grep pattern")


def test_bash_prefix_rule_rejects_compound_command():
    assert not bash_rule_matches("Bash(cd:*)", "cd /tmp && rm -rf x")
    assert not bash_rule_matches("Bash(git:*)" , "git status; curl evil")


def test_bash_dangerous_removal_detection():
    assert is_dangerous_removal("rm -rf /")
    assert is_dangerous_removal("rm -rf /*")
    assert is_dangerous_removal("rm -rf ~")
    assert is_dangerous_removal("rmdir /usr")
    assert not is_dangerous_removal("rm -rf build")
    assert not is_dangerous_removal("rm a.txt")


def test_bash_read_only_detection():
    assert is_read_only("ls -la")
    assert is_read_only("cat a.txt | head -3")
    assert is_read_only("git status")
    assert is_read_only("git diff --stat")
    assert not is_read_only("echo $HOME")
    assert not is_read_only("ls > out.txt")
    assert not is_read_only("find . -delete")
    assert not is_read_only("cd /tmp && git status")
    assert not is_read_only("python3 -c 'x'")
    assert not is_read_only("rm a.txt")


def test_bash_decision_order_deny_ask_allow_readonly():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, deny=["Bash(rm:*)"],
                                 allow=["Bash(npm ci)"], ask=["Bash(git push:*)"])
        assert g.decide("Bash", {"command": "rm -rf x"}) == "deny"
        assert g.decide("Bash", {"command": "git push origin main"}) == "ask"
        assert g.decide("Bash", {"command": "npm ci"}) == "allow"
        assert g.decide("Bash", {"command": "ls -la"}) == "allow"
        assert g.decide("Bash", {"command": "make build"}) == "ask"

        both = PermissionController(mode="default", cwd=d,
                                    deny=["Bash(rm:*)"], allow=["Bash(rm:*)"])
        assert both.decide("Bash", {"command": "rm -rf x"}) == "deny"


def test_bash_env_and_wrapper_stripping():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, allow=["Bash(npm ci)"],
                                 deny=["Bash(curl:*)"])
        assert g.decide("Bash", {"command": "NODE_ENV=prod npm ci"}) == "allow"
        assert g.decide("Bash", {"command": "timeout 5 curl http://x"}) == "deny"
        assert g.decide("Bash", {"command": "FOO=bar curl http://x"}) == "deny"
        assert g.decide("Bash", {"command": "FOO=bar npm ci"}) == "ask"


def test_bash_dangerous_removal_asks_even_when_allowed():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, allow=["Bash(rm:*)"])
        assert g.decide("Bash", {"command": "rm -rf build"}) == "allow"
        assert g.decide("Bash", {"command": "rm -rf /"}) == "ask"


def test_bash_dangerous_removal_is_not_remembered():
    with tempfile.TemporaryDirectory() as d:

        async def once(name, inp):
            return "dont_ask"

        g = PermissionController(mode="default", cwd=d, request=once)
        assert asyncio.run(g.authorize("Bash", {"command": "rm -rf /"})) is True
        assert g._allow == []
        assert asyncio.run(
            g.authorize("Bash", {"command": "npm ci"})) is True
        assert g._allow == ["Bash(npm ci:*)"]


def test_bash_accept_edits_mode_allows_workspace_file_commands():
    with tempfile.TemporaryDirectory() as d:
        ae = PermissionController(mode="acceptEdits", cwd=d)
        assert ae.decide("Bash", {"command": "mkdir out"}) == "allow"
        assert ae.decide("Bash", {"command": "mv a b"}) == "allow"
        assert ae.decide("Bash", {"command": "rm ../outside"}) == "ask"
        assert ae.decide("Bash", {"command": "python3 x.py"}) == "ask"


def test_bash_bare_deny_removes_tool_but_rule_does_not():
    with tempfile.TemporaryDirectory() as d:
        bare = PermissionController(mode="default", cwd=d, deny=["Bash"])
        assert bare.allowed_tool("Bash") is False
        rule = PermissionController(mode="default", cwd=d, deny=["Bash(rm:*)"])
        assert rule.allowed_tool("Bash") is True


def test_build_team_wires_bash_through_permission_hook():
    async def main():
        from pyclaw.agents import build_team
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                              deny=["Bash(curl:*)"])
            names = [t["name"] for t in team.tool_schemas()]
            blocked = await team.execute_tool(
                "Bash", {"command": "curl http://example.com"}, team.lead)
            allowed = await team.execute_tool(
                "Bash", {"command": "echo hi"}, team.lead)
            return names, blocked, allowed

    names, blocked, allowed = asyncio.run(main())
    assert "Bash" in names
    assert "hook blocked" in blocked
    assert "hi" in allowed


def test_build_team_removes_bash_when_denied_by_bare_name():
    async def main():
        from pyclaw.agents import build_team
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                              deny=["Bash"])
            return [t["name"] for t in team.tool_schemas()]

    assert "Bash" not in asyncio.run(main())


def test_dont_ask_persists_allow():
    with tempfile.TemporaryDirectory() as d:

        async def once(name, inp):
            return "dont_ask"

        g = PermissionController(mode="default", cwd=d, request=once)
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True
        assert "Edit" in g._allow
        assert g.decide("Edit", {"file_path": "a.txt"}) == "allow"
        data = json.read(Path(d) / ".pyclaw" / "settings.local.json")
        assert data["permissions"]["allow"] == ["Edit"]


def test_suggested_rule_prefers_two_word_prefix():
    from pyclaw.tools.coding.shell_rules import suggested_rule
    assert suggested_rule('git commit -m "fix"') == 'Bash(git commit:*)'
    assert suggested_rule('npm run test') == 'Bash(npm run:*)'
    assert suggested_rule('timeout 10 git push') == 'Bash(git push:*)'
    assert bash_rule_matches('Bash(git commit:*)', 'git commit -m "fix"')


def test_suggested_rule_exact_fallback():
    from pyclaw.tools.coding.shell_rules import suggested_rule
    assert suggested_rule('ls -la') == 'Bash(ls -la)'
    assert suggested_rule('python3 x.py') == 'Bash(python3 x.py)'
    assert suggested_rule('mkdir a && mkdir b') == 'Bash(mkdir a && mkdir b)'
    assert bash_rule_matches('Bash(ls -la)', 'ls -la')
    assert bash_rule_matches('Bash(mkdir a && mkdir b)', 'mkdir a && mkdir b')


def test_suggested_rule_refuses_risky_commands():
    from pyclaw.tools.coding.shell_rules import suggested_rule
    assert suggested_rule('rm -rf /') is None            # 危险删除
    assert suggested_rule('FOO=bar npm test') is None    # 不安全 env 前缀
    assert suggested_rule('echo `whoami`') is None       # 无法安全解析
    assert suggested_rule('sudo rm x') == 'Bash(sudo rm x)'  # 裸 shell 只给精确


def test_dont_ask_saves_rule_to_local_settings():
    with tempfile.TemporaryDirectory() as d:

        async def always(name, inp):
            return "dont_ask"

        g = PermissionController(mode="default", cwd=d, request=always)
        assert asyncio.run(
            g.authorize("Bash", {"command": "git commit -m x"})) is True
        data = json.read(Path(d) / ".pyclaw" / "settings.local.json")
        assert data["permissions"]["allow"] == ["Bash(git commit:*)"]
        # 重启（新 controller）后不再问
        g2 = PermissionController(mode="default", cwd=d)
        assert g2.decide("Bash", {"command": "git commit -m x"}) == "allow"


def test_rules_load_from_user_and_local_settings(tmp_path, monkeypatch):
    from pyclaw.tools.coding import permission as perm
    user_file = tmp_path / "user-settings.json"
    json.write(user_file, {"permissions": {
        "allow": ["Bash(npm run:*)"], "deny": ["Bash(curl:*)"]}})
    monkeypatch.setattr(perm, "_user_settings_file", lambda: user_file)

    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / ".pyclaw" / "settings.local.json"
        local.parent.mkdir(parents=True)
        json.write(local, {"permissions": {"deny": ["Bash(npm run:*)"]}})
        g = perm.PermissionController(mode="default", cwd=d)
        # local 层的 deny 覆盖 user 层的 allow（later source wins，deny 优先求值）
        assert g.decide("Bash", {"command": "npm run test"}) == "deny"
        assert g.decide("Bash", {"command": "curl http://x"}) == "deny"
        assert g.decide("Bash", {"command": "node server.js"}) == "ask"


def test_rule_listing_reports_sources(tmp_path, monkeypatch):
    from pyclaw.tools.coding import permission as perm
    user_file = tmp_path / "user-settings.json"
    json.write(user_file, {"permissions": {"allow": ["Bash(npm run:*)"]}})
    monkeypatch.setattr(perm, "_user_settings_file", lambda: user_file)

    with tempfile.TemporaryDirectory() as d:
        g = perm.PermissionController(mode="default", cwd=d,
                                      deny=["Bash(curl:*)"])
        rules = g.rule_listing()
        assert ('allow', 'Bash(npm run:*)', 'user') in rules
        assert ('deny', 'Bash(curl:*)', 'cli') in rules


# --- 后台任务（claude run_in_background / TaskOutput / TaskStop） ---

def test_bash_run_in_background_returns_immediately():
    import time as _time
    from pyclaw.tools.coding import background
    bash = _tools("/tmp")["Bash"]
    started = _time.monotonic()
    out = bash(command="echo bg-done-42", run_in_background=True)
    elapsed = _time.monotonic() - started
    assert elapsed < 5                       # 不等命令结束
    match = re.search(r"ID: (b[0-9a-z]{8})", out)
    assert match, out
    task_id = match.group(1)
    assert "Output is being written to:" in out
    assert task_id in background._tasks
    task = background._tasks[task_id]
    for _ in range(50):
        if task["process"].poll() is not None:
            break
        _time.sleep(0.1)
    assert task["process"].poll() == 0
    assert "bg-done-42" in task["output"].read_text(encoding="utf-8")


def test_task_output_blocks_until_completion():
    from pyclaw.tools.coding import background
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 0.4 && echo finished-data", run_in_background=True)
    task_id = re.search(r"ID: (b[0-9a-z]{8})", out).group(1)
    text = _tools("/tmp")["TaskOutput"](task_id=task_id, block=True, timeout=5000)
    assert "finished-data" in text
    assert "<exit_code>0</exit_code>" in text


def test_task_output_timeout_reports_still_running():
    from pyclaw.tools.coding import background
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 5", run_in_background=True)
    task_id = re.search(r"ID: (b[0-9a-z]{8})", out).group(1)
    text = _tools("/tmp")["TaskOutput"](task_id=task_id, block=True, timeout=300)
    assert "still running" in text
    assert "exit_code" not in text
    background.cleanup_background_tasks()


def test_task_stop_kills_process_group():
    import time as _time
    from pyclaw.tools.coding import background
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 30", run_in_background=True)
    task_id = re.search(r"ID: (b[0-9a-z]{8})", out).group(1)
    task = background._tasks[task_id]
    text = _tools("/tmp")["TaskStop"](task_id=task_id)
    assert f"Successfully stopped task: {task_id}" in text
    assert "sleep 30" in text
    for _ in range(30):
        if task["process"].poll() is not None:
            break
        _time.sleep(0.1)
    assert task["process"].poll() is not None   # 进程已死
    assert task["killed"] is True


def test_background_tasks_cleanup_kills_all():
    from pyclaw.tools.coding import background
    bash = _tools("/tmp")["Bash"]
    bash(command="sleep 30", run_in_background=True)
    bash(command="sleep 30", run_in_background=True)
    tasks = list(background._tasks.values())
    background.cleanup_background_tasks()
    for task in tasks:
        assert task["process"].poll() is not None
    assert background._tasks == {}


def test_task_output_unknown_task():
    from pyclaw.tools.coding import background
    text = _tools("/tmp")["TaskOutput"](task_id="bdeadbeef")
    assert "no such background task" in text
    text = _tools("/tmp")["TaskStop"](task_id="bdeadbeef")
    assert "no such background task" in text


def test_background_tools_registered_by_build_coding_tools():
    names = {t.name for t in build_coding_tools("/tmp")}
    assert {"TaskOutput", "TaskStop"} <= names