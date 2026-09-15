from __future__ import annotations

import re
from pathlib import Path

from chatchat.core.agents import AgentDefinition

_FRONTMATTER = re.compile(r'^---\s*\n([\s\S]*?)\n---\s*\n?')


def _user_agents_dir() -> Path:
    from pyclaw import __pyclaw_home__
    return Path(__pyclaw_home__) / 'agents'


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER.match(text)
    if m is None:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(':')
        if key.strip():
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[m.end():].strip()


def _parse_tools_field(raw: str) -> list[str] | None:
    raw = (raw or '').strip()
    if not raw:
        return None
    if raw == '*':
        return ['*']
    return [t.strip() for t in raw.split(',') if t.strip()]


def _definition_from_md(text: str, all_tools: list):
    meta, body = _parse_frontmatter(text)
    name = meta.get('name')
    if not name:
        return None
    description = meta.get('description', '')
    raw_tools = _parse_tools_field(meta.get('tools', ''))
    by_name = {t.name: t for t in all_tools}
    if raw_tools is None or raw_tools == ['*']:
        tools = list(all_tools)
    else:
        tools = [by_name[n] for n in raw_tools if n in by_name]
    return AgentDefinition(name, system_prompt=body, tools=tools,
                           model=meta.get('model') or None,
                           description=description)


def load_agent_defs(cwd: str, all_tools: list) -> list[AgentDefinition]:
    defs: list[AgentDefinition] = []
    seen: set[Path] = set()
    for directory in (_user_agents_dir(), Path(cwd) / '.pyclaw' / 'agents'):
        try:
            files = sorted(Path(directory).glob('*.md'))
        except OSError:
            continue
        for path in files:
            try:
                if path.resolve() in seen:
                    continue
                seen.add(path.resolve())
                text = path.read_text(encoding='utf-8')
            except OSError:
                continue
            defn = _definition_from_md(text, all_tools)
            if defn is not None:
                defs.append(defn)
    return defs
