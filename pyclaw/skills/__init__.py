from pathlib import Path

from chatchat.core.skills import SkillRegistry

skill_roots: list[str] = []


def discover_registry(cwd, extra=None) -> SkillRegistry:
    """Project skills win over the user's, and both over a plugin's."""
    from pyclaw import pyclaw_home
    from pyclaw.plugins import discover_skills
    roots = [(Path(cwd) / '.pyclaw' / 'skills', 'project'),
             (pyclaw_home() / 'skills', 'user')]
    contributed = list(extra if extra is not None else skill_roots)
    if extra is None:
        contributed += list(discover_skills())
    roots += [(Path(root), 'plugin') for root in contributed]
    return SkillRegistry.load(roots)
