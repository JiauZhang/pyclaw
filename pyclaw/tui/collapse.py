from __future__ import annotations

from pyclaw.tui.theme import GROUP_PARTS, ROLLUP_KINDS


def group_text(counts: dict, *, active: bool, markup: bool = True) -> str:
    chunks = []
    for kind, active_verb, done_verb, noun, plural in GROUP_PARTS:
        count = counts.get(kind, 0)
        if not count:
            continue
        verb = active_verb if active else done_verb
        verb = verb[0].upper() + verb[1:] if not chunks else \
            verb[0].lower() + verb[1:]
        shown = f"[bold]{count}[/]" if markup else str(count)
        chunks.append(f"{verb} {shown} {noun if count == 1 else plural}")
    return ", ".join(chunks)


class Group:

    def __init__(self):
        self.counts = {kind: 0 for kind, *_ in GROUP_PARTS}
        self.entries = []
        self.read_keys = set()
        self.active = True

    def add(self, kinds, key, uid):
        self.entries.append((kinds, key, uid))
        for kind in kinds:
            if kind == 'read':
                if key in self.read_keys:
                    continue
                self.read_keys.add(key)
            self.counts[kind] = self.counts.get(kind, 0) + 1

    def summary(self) -> str:
        return group_text(self.counts, active=self.active)


def recent_rollup(recent: list) -> str:
    counts = {}
    for kinds in reversed(recent):
        if not kinds or not kinds <= ROLLUP_KINDS:
            break
        for kind in kinds:
            counts[kind] = counts.get(kind, 0) + 1
    if sum(counts.values()) < 2:
        return ''
    return group_text(counts, active=True, markup=False)
