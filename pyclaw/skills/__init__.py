import logging
from pathlib import Path
from typing import Any, List, Optional

from chatchat.agent import SubAgent
from chatool.skills import skills as _chatool_skill_roots

from pyclaw.tools import tools as default_tools

logger = logging.getLogger(__name__)


def load_skills_as_tools(
    provider: str,
    model: str,
    available_tools: Optional[List[Any]] = None,
) -> list:
    tools = available_tools if available_tools is not None else default_tools

    instances = []
    seen = set()
    for root in _chatool_skill_roots:
        for md_path in sorted(Path(root).rglob('SKILL.md')):
            key = str(md_path.parent)
            if key in seen:
                continue
            seen.add(key)
            sub = SubAgent.from_skill(
                key,
                provider=provider,
                model=model,
                available_tools=tools,
            )
            logger.info('Loaded skill: %s from %s', sub.name, key)
            instances.append(sub)
    return instances