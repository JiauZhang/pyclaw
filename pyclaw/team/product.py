from __future__ import annotations

import os

from chatchat.team.product import TeamProduct

from pyclaw.permissions import parse_mode


class PyclawProduct(TeamProduct):
    """What pyclaw adds to a runtime team: a permission gate, the agent mode,
    the agent definitions that came from the command line, and the memory roots
    that follow the working directory.

    The runtime calls into this instead of a host reaching into the team: moving
    the directory, changing the plan mode and granting a skill's tools all land
    here, where the product decides what they mean.
    """

    def __init__(self, team, *, gate, mode: str, agent_memory, instruction: str):
        self.team = team
        self.gate = gate
        self.mode = mode
        self.agent_memory = agent_memory
        self.instruction = instruction
        self.cli_agent_defs: list = []
        self.snapshot_updates: list[str] = []
        self._after_cwd: list = []

    def after_cwd_change(self, listener) -> None:
        self._after_cwd.append(listener)

    def reload_roots(self, cwd) -> None:
        """Re-read what the session knows about the directory it works in."""
        cwd = str(cwd)
        self.team.set_instruction_files(self.agent_memory.load_instruction_files(cwd))
        memory = self.agent_memory.load_project_memory(cwd)
        self.team.set_lead_instruction(self.instruction + (f"\n\n{memory}" if memory else ""))
        self.team.rules = self.agent_memory.rule_set(cwd)

    def cwd_changed(self, cwd) -> None:
        self.gate.move_to(cwd)
        os.chdir(cwd)
        self.reload_roots(cwd)
        for listener in self._after_cwd:
            listener(cwd)

    def plan_mode_changed(self, mode: str) -> None:
        self.gate.mode = parse_mode(mode)
        self.team.hooks.permission_mode = self.gate.mode.value

    def skills_granted(self, tools) -> None:
        self.gate.grant_rules(tools)
