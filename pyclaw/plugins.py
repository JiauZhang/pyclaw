import pathlib

from conippets.plugin import collect
from importlib.metadata import entry_points

TOOLS_GROUP = 'pyclaw.tools'
SKILLS_GROUP = 'pyclaw.skills'
AGENTS_GROUP = 'pyclaw.agents'


def discover_tools():
    return collect(TOOLS_GROUP, 'tools', key=lambda t: t.name)


def discover_skills():
    return collect(SKILLS_GROUP, 'skills')


def discover_agent_dirs():
    """Packages on the pyclaw.agents group expose AGENTS_DIR: the package
    directory whose *.md files each define one agent (frontmatter +
    prompt body), mirroring how claude-code plugins ship agents."""
    dirs = []
    for ep in entry_points(group=AGENTS_GROUP):
        try:
            module = ep.load()
        except Exception:
            continue
        directory = getattr(module, 'AGENTS_DIR', None)
        if directory is not None:
            dirs.append((pathlib.Path(directory), ep.name))
    return dirs
