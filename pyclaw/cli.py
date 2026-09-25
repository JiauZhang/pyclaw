
from __future__ import annotations

import subprocess
from typing import List, Optional

from pyclaw import config


def find_listener_pids(port: int) -> List[int]:
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if not out.stdout.strip():
        return []
    return [int(p) for p in out.stdout.split() if p.strip().isdigit()]


def find_serve_pids() -> List[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "pyclaw serve"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if not out.stdout.strip():
        return []
    return [int(p) for p in out.stdout.split() if p.strip().isdigit()]


def kill_pids(pids: List[int], force: bool = False) -> List[int]:
    killed: List[int] = []
    for pid in pids:
        try:
            subprocess.run(["kill", "-9" if force else "-15", str(pid)], check=False)
            killed.append(pid)
        except FileNotFoundError:
            break
    return killed


def stop_server(
    port: Optional[int] = None,
    force: bool = False,
    all_processes: bool = False,
) -> List[int]:
    if all_processes:
        targets = find_serve_pids()
    else:
        if port is None:
            port = config.load().get("gateway", {}).get("http", {}).get("port")
        resolved = port or 12321
        targets = find_listener_pids(resolved)

    if not targets:
        return []
    return kill_pids(targets, force=force)
