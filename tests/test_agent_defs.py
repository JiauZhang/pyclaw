import asyncio
import tempfile
from pathlib import Path

from conippets import json  # noqa: F401

from pyclaw.agent_defs import load_agent_defs
from pyclaw.agents import build_team
from pyclaw.tools.coding import build_coding_tools


def _write_agent(root: Path, filename: str, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(text, encoding='utf-8')


REVIEWER = '''---
name: reviewer
description: Reviews code changes for correctness and style
tools: Read, Glob, Grep, Bash
---

You are a strict code reviewer. Report issues only.
'''

WILDCARD = '''---
name: researcher
description: Deep research on any topic
tools: '*'
---

Research thoroughly and cite sources.
'''

NO_TOOLS = '''---
name: planner
description: Drafts implementation plans without touching files
---

Plan first, edit never.
'''


def test_load_agent_defs_parses_and_resolves_tools():
    with tempfile.TemporaryDirectory() as d:
        agents_dir = Path(d) / '.pyclaw' / 'agents'
        _write_agent(agents_dir, 'reviewer.md', REVIEWER)
        _write_agent(agents_dir, 'researcher.md', WILDCARD)
        _write_agent(agents_dir, 'planner.md', NO_TOOLS)
        all_tools = build_coding_tools(d)
        defs = {x.agent_type: x for x in load_agent_defs(d, all_tools=all_tools)}
        assert set(defs) >= {'reviewer', 'researcher', 'planner'}
        assert {t.name for t in defs['reviewer'].tools} <= {t.name for t in all_tools}
        assert 'Read' in {t.name for t in defs['reviewer'].tools}
        assert len(defs['researcher'].tools) == len(all_tools)
        assert len(defs['planner'].tools) == len(all_tools)
        assert 'strict code reviewer' in defs['reviewer'].system_prompt
        assert defs['reviewer'].description == \
            'Reviews code changes for correctness and style'


def test_load_agent_defs_skips_invalid_and_project_overrides_user(tmp_path,
                                                                   monkeypatch):
    from pyclaw import agent_defs as mod
    user_dir = tmp_path / 'user-agents'
    _write_agent(user_dir, 'reviewer.md', REVIEWER)
    _write_agent(user_dir, 'no-name.md', 'just some docs, no frontmatter name')
    _write_agent(user_dir, 'bad.md', '---\ndescription: no name here\n---\nbody')
    monkeypatch.setattr(mod, '_user_agents_dir', lambda: user_dir)

    with tempfile.TemporaryDirectory() as d:
        project = Path(d) / '.pyclaw' / 'agents'
        _write_agent(project, 'reviewer.md',
                     REVIEWER.replace('strict code reviewer',
                                      'project-level reviewer'))
        defs = {x.agent_type: x for x in load_agent_defs(d, all_tools=[])}
        assert 'project-level reviewer' in defs['reviewer'].system_prompt
        assert 'reviewer' in defs and 'no-name' not in defs


def test_build_team_registers_agent_defs_and_lists_in_tool_description(tmp_path):
    _write_agent(tmp_path / '.pyclaw' / 'agents', 'reviewer.md', REVIEWER)

    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path))
        schema = next(t for t in team.tool_schemas() if t['name'] == 'create_agent')
        return team.agent_defs.get('reviewer'), schema['description']

    defn, description = asyncio.run(main())
    assert defn.agent_type == 'reviewer'
    assert 'reviewer' in description
    assert 'Reviews code changes' in description
