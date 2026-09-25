from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

CANDIDATES = ('code', 'vi', 'nano')

WAIT_FLAGS = {'code': ['-w'], 'subl': ['--wait']}


def external_editor() -> str:
    for name in ('VISUAL', 'EDITOR'):
        value = os.environ.get(name, '').strip()
        if value:
            return value
    return next((cmd for cmd in CANDIDATES if shutil.which(cmd)), '')


def open_file(path: Path) -> str:
    editor = external_editor()
    if not editor:
        return ''
    parts = editor.split()
    argv = [*parts, *WAIT_FLAGS.get(parts[0], []), str(path)]
    subprocess.run(argv, check=False)
    return editor
