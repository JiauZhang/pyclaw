from __future__ import annotations

import subprocess
from pathlib import Path

from pyclaw.session.store import (
    adopt_artifacts,
    rename_session,
    title_of,
    create_branch,
    load_transcript)


class HistoryMixin:

    def turns(self) -> list:
        return self._team.turns()
    def rewind_stats(self, mark: int) -> dict | None:
        history = self._team.file_history
        return None if history is None else history.diff_stats(mark)
    def diff(self) -> dict:
        cwd = Path(self._team.tool_context.cwd)

        def git(*args):
            try:
                done = subprocess.run(('git', *args), cwd=str(cwd),
                                      capture_output=True, text=True)
            except OSError:
                return None
            return done if done.returncode == 0 else None

        status = git('status', '--porcelain')
        if status is not None:
            changed = sorted(line[3:].strip() for line in
                             status.stdout.splitlines() if line[1:2] != '?')
            untracked = sorted(line[3:].strip() for line in
                               status.stdout.splitlines()
                               if line.startswith('?? '))
            body = git('diff')
            text = (body.stdout if body else '')
            if untracked:
                text += ('' if not text else '\n') + '\n'.join(
                    f'?? {name} (not in git yet)' for name in untracked)
            return {'kind': 'git', 'files': changed + untracked,
                    'text': text or 'The working tree matches the last commit.'}
        history = self._team.file_history
        stats = history.session_stats() if history is not None else {}
        if not stats:
            return {'kind': 'session', 'files': [],
                    'text': 'Nothing has been changed in this session.'}
        return {'kind': 'session', 'files': sorted(stats),
                'text': '\n'.join(
                    f"{name} \u00b7 +{entry['insertions']} "
                    f"-{entry['deletions']}"
                    for name, entry in sorted(stats.items()))}
    def rewind(self, mark: int, *, code: bool = True,
               conversation: bool = True) -> dict:
        result = self._team.rewind(mark, code=code, conversation=conversation)
        if result['messages']:
            self.save_transcript()
        return result

    def rename(self, title: str) -> str:

        return rename_session(self.conv_session_id, title)

    @property
    def title(self) -> str:

        return title_of(self.conv_session_id)

    def branch(self, title: str = '') -> dict:

        fork = create_branch(self.conv_session_id, title)
        messages = load_transcript(fork['id'])
        self._team.restore(messages)
        self._team.reset_rules()
        self._team.begin_new_session('branch')
        self.conv_session_id = fork['id']
        adopt_artifacts(fork['forked_from'], fork['id'])
        self._adopt_history()
        self.resume_from = None
        fork['messages'] = len(messages)
        return fork
