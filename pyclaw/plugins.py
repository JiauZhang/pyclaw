from conippets.plugin import collect

TOOLS_GROUP = 'pyclaw.tools'
SKILLS_GROUP = 'pyclaw.skills'
AGENTS_GROUP = 'pyclaw.agents'


def discover_tools():
    return collect(TOOLS_GROUP, 'tools', key=lambda t: t.name)


def discover_skills():
    return collect(SKILLS_GROUP, 'skills')


def discover_agents():
    return collect(AGENTS_GROUP, 'agents', key=lambda a: a['name'])