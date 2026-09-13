from __future__ import annotations

from .background import make_task_output, make_task_stop
from .edit import make_edit, make_multi_edit, make_write
from .permission import (PermissionController, PermissionMode, WRITE_TOOLS,
                         next_mode, parse_mode)
from .search import make_glob, make_grep, make_ls, make_read
from .shell import make_bash

__all__ = [
    'PermissionController',
    'PermissionMode',
    'WRITE_TOOLS',
    'next_mode',
    'parse_mode',
    'build_coding_tools',
]


def build_coding_tools(cwd: str):
    return [
        make_read(cwd),
        make_glob(cwd),
        make_grep(cwd),
        make_ls(cwd),
        make_write(cwd),
        make_edit(cwd),
        make_multi_edit(cwd),
        make_bash(cwd),
        make_task_output(cwd),
        make_task_stop(cwd),
    ]
