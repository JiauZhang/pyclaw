from __future__ import annotations

import atexit
import os
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from chatchat.team.host_tasks import HostTasks
from chatchat.tool import ToolResult

from pyclaw import task_registry as tasks

TASK_OUTPUT_TAIL_CHARS = 30_000
TASK_BLOCK_POLL_S = 0.1
TASK_DEFAULT_TIMEOUT_MS = 30_000
TASK_MAX_TIMEOUT_MS = 600_000
_notifier = None


def set_notifier(cb) -> None:
    global _notifier
    _notifier = cb


def _watch(task_id: str):
    record = task(task_id)
    if record is None:
        return
    code = record.payload["process"].wait()
    killed = bool(record.payload.get("killed"))
    if record.status not in (tasks.COMPLETED, tasks.FAILED, tasks.KILLED):
        if killed:
            record.finish(tasks.KILLED)
        else:
            record.finish(tasks.COMPLETED if code == 0 else tasks.FAILED)
    cb = _notifier
    if cb is not None:
        try:
            cb(task_id, record.label, code, killed)
        except Exception:
            pass


def _tasks_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / "pyclaw-tasks"
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def scratch_path() -> Path:
    return _tasks_dir() / f"{secrets.token_hex(8)}.output"


def _output_path(task_id: str) -> Path:
    return _tasks_dir() / f"{task_id}.output"


def output_of(task_id: str) -> str:
    return _tail(_output_path(task_id))


def task(task_id: str):
    record = tasks.registry().get(task_id)
    return record if record is not None and record.kind == "shell" else None


def _record(task_id: str, command: str, process, output: Path):
    return tasks.registry().register(
        tasks.Task(
            id=task_id,
            kind="shell",
            label=command,
            status=tasks.RUNNING,
            output=str(output),
            payload={"process": process, "killed": False},
        )
    )


def adopt(command: str, process, output: Path) -> str:
    task_id = tasks.new_id("shell")
    _record(task_id, command, process, output)
    threading.Thread(target=_watch, args=(task_id,), daemon=True).start()
    return task_id


def spawn(cwd: str, command: str) -> str:
    task_id = tasks.new_id("shell")
    path = _output_path(task_id)
    handle = open(path, "ab")
    try:
        process = subprocess.Popen(command, shell=True, cwd=cwd, stdout=handle, stderr=handle, start_new_session=True)
    finally:
        handle.close()
    _record(task_id, command, process, path)
    threading.Thread(target=_watch, args=(task_id,), daemon=True).start()
    return task_id


def _tail(path: Path, limit: int = TASK_OUTPUT_TAIL_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} chars of earlier output omitted ...]\n" + text[-limit:]


def row(record) -> dict:
    now = time.monotonic()
    code = record.payload["process"].poll()
    return {
        "kind": "shell",
        "id": record.id,
        "label": record.label,
        "command": record.label,
        "seconds": record.seconds(now),
        "exit": code,
        "killed": record.status == tasks.KILLED,
        "detail": (f"running {record.seconds(now)}s" if code is None else f"exited {code}"),
        "stoppable": code is None,
    }


def snapshot() -> list[dict]:
    now = time.monotonic()
    rows = []
    for record in tasks.registry().of_kind("shell"):
        rows.append(
            {
                "id": record.id,
                "command": record.label,
                "seconds": record.seconds(now),
                "exit": record.payload["process"].poll(),
                "killed": record.status == tasks.KILLED,
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def kill_process(process) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        process.kill()


def stop(task_id: str) -> dict | None:
    record = task(task_id)
    if record is None:
        return None
    process = record.payload["process"]
    if process.poll() is None:
        kill_process(process)
        record.payload["killed"] = True
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    if record.status not in (tasks.COMPLETED, tasks.FAILED, tasks.KILLED):
        record.finish(tasks.KILLED if record.payload["killed"] else (tasks.COMPLETED if process.poll() == 0 else tasks.FAILED))
    return next((row for row in snapshot() if row["id"] == task_id), None)


def cleanup_background_tasks():
    for record in tasks.registry().of_kind("shell"):
        process = record.payload["process"]
        if process.poll() is None:
            kill_process(process)
            record.payload["killed"] = True
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        tasks.registry().forget(record.id)


atexit.register(cleanup_background_tasks)


class ShellTasks(HostTasks):
    """The shell commands pyclaw started in the background, offered to the
    runtime's task tools so one TaskOutput and one TaskStop cover them and the
    sub-agents alike - which is what the tool's description has always promised."""

    def has(self, task_id: str) -> bool:
        return task(task_id) is not None

    def read(self, task_id: str, *, block: bool = True, timeout_ms: int = TASK_DEFAULT_TIMEOUT_MS):
        record = task(task_id)
        if record is None:
            return None
        limit = max(1, min(int(timeout_ms), TASK_MAX_TIMEOUT_MS)) / 1000
        deadline = time.monotonic() + (limit if block else 0)
        while True:
            if record.payload["process"].poll() is not None:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(TASK_BLOCK_POLL_S)
        code = record.payload["process"].poll()
        if code is not None:
            retrieval, status = "success", "completed"
        elif block:
            retrieval, status = "timeout", "running"
        else:
            retrieval, status = "not_ready", "running"
        lines = [
            f"<fetch_result>{retrieval}</fetch_result>",
            f"<task_ref>{task_id}</task_ref>",
            "<task_kind>shell</task_kind>",
            f"<run_state>{status}</run_state>",
        ]
        if code is not None:
            lines.append(f"<return_code>{code}</return_code>")
        body = _tail(Path(record.output)).rstrip("\n")
        lines.append(body if body.strip() else "(no output yet)")
        meta = {"status": status}
        if code is not None:
            meta["exit_code"] = code
        return ToolResult(text="\n".join(lines), meta=meta)

    def stop(self, task_id: str) -> str:
        row = stop(task_id)
        if row is None:
            return f"Error: no such background task: {task_id}"
        return ToolResult(text=f"Stopped {task_id} ({row['command']})", meta={"stopped": True})
