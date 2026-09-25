from pyclaw.export import write, copy_targets, replies
from pyclaw.notify import clipboard

# Commands that take the conversation somewhere else: export and copy.
def _export(session, arg: str, session_key: str) -> str:
    import os


    transcript = session.transcript()
    if not transcript:
        return 'Nothing to export yet.'
    path = write(transcript, session_id=str(session_key or session.name),
                 name=os.path.expanduser(arg) if arg else '')
    return f'Exported {len(transcript)} messages to {path}'

def _copy(session, arg: str, terminal=None) -> str:

    found = replies(session.transcript())
    if not found:
        return 'Nothing to copy yet.'
    which, _, block = str(arg or '').partition(':')
    try:
        picked = int(which) if which else 1
        wanted = int(block) if block else 0
    except ValueError:
        return 'copy takes n, or n:block, both counted from 1'
    if not 1 <= picked <= len(found):
        return f'There are {len(found)} answers to copy, not number {picked}.'
    targets = copy_targets(session.transcript(), which=picked, block=wanted)
    if not targets:
        return f'No code block {wanted} in that answer.'
    label, text = targets[0]
    lines = text.count('\n') + 1
    if terminal is not None:
        terminal(clipboard(text))
    written = _copy_to_file(text, label)
    where = f'\nAlso written to {written}' if written else ''
    return (f'Copied the {label} to the clipboard ({len(text)} characters, '
            f'{lines} lines){where}')

def _copy_to_file(text: str, label: str):
    import tempfile
    from pathlib import Path

    name = 'response.md' if label == 'whole reply' else f'code-{label}'
    directory = Path(tempfile.gettempdir()) / 'pyclaw'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        target.write_text(text, encoding='utf-8')
    except OSError:
        return None
    return target
