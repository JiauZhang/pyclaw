from __future__ import annotations

import inspect
from pathlib import Path

from chatchat.tool import Tool, no_arguments, tool

from .names import ENTER_PLAN_MODE, EXIT_PLAN_MODE

ENTER_TEXT = (
    "Entered plan mode. Explore the codebase and design the approach first: "
    "no write or state-changing tool runs until the plan is approved. Write "
    "the plan to {path}, then call ExitPlanMode to present it."
)

APPROVE = "Yes, implement it"

AUTO_ACCEPT = "Yes, and apply the edits without asking"

KEEP_PLANNING = "No, keep planning"


def plan_file(team) -> Path | None:
    gate = getattr(getattr(team, "product", None), "gate", None)
    path = getattr(gate, "plan_file", None)
    return Path(path) if path else None


def read_plan(team) -> str:
    path = plan_file(team)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def plan_question() -> dict:
    return {
        "question": "Approve this plan?",
        "header": "Plan",
        "options": [
            {"label": APPROVE, "description": "Leave plan mode and start implementing."},
            {"label": AUTO_ACCEPT, "description": "Leave plan mode; file edits run without asking."},
            {"label": KEEP_PLANNING, "description": "Stay in plan mode."},
        ],
    }


@tool(
    name=ENTER_PLAN_MODE,
    description="""Ask to switch this session into plan mode before starting anything but a trivial change.
In plan mode nothing that writes or changes state runs, so you can explore and design the approach
and have it approved before touching the project.""",
    parameters=no_arguments(),
)
async def enter_plan_mode(context) -> str:
    team = getattr(context, "team", None)
    product = getattr(team, "product", None)
    gate = getattr(product, "gate", None)
    if gate is None:
        return "Error: this session has no permission gate, so plan mode is unavailable."
    if str(gate.mode.value) == "plan":
        return "Already in plan mode."
    product.plan_mode_changed("plan")
    path = plan_file(team)
    if path is None:
        return "Error: this session has no plan file."
    return ENTER_TEXT.format(path=path)


@tool(
    name=EXIT_PLAN_MODE,
    description="Present the plan you wrote to the user and leave plan mode once they approve it.",
    parameters=no_arguments(),
)
async def exit_plan_mode(context) -> str:
    team = getattr(context, "team", None)
    product = getattr(team, "product", None)
    path = plan_file(team)
    if path is None:
        return "Error: this session has no plan file."
    plan = read_plan(team)
    if not plan:
        return f"Error: nothing has been written to {path} yet."
    asker = getattr(team, "user_input", None)
    if asker is None:
        return "Error: no one can approve a plan in this session."
    answers = asker(getattr(context, "agent", None), [plan_question()])
    if inspect.isawaitable(answers):
        answers = await answers
    choice = str(answers[0] if answers else "")
    if choice == AUTO_ACCEPT:
        product.plan_mode_changed("acceptEdits")
        return f"The user approved the plan and wants the edits applied without further prompts.\n\n{plan}"
    if choice == APPROVE:
        product.plan_mode_changed("default")
        return f"The user approved the plan.\n\n{plan}"
    return "The user did not approve the plan. Stay in plan mode and ask what they want changed."


def tools(team) -> list[Tool]:
    gate = getattr(getattr(team, "product", None), "gate", None)
    return [enter_plan_mode, exit_plan_mode] if gate is not None else []
