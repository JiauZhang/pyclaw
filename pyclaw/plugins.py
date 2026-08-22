from conippets.plugin import collect

TOOLS_GROUP = 'pyclaw.tools'
SKILLS_GROUP = 'pyclaw.skills'


def discover_tools():
    return collect(TOOLS_GROUP, 'tools', key=lambda t: t.name)


def discover_skills():
    return collect(SKILLS_GROUP, 'skills')