from __future__ import annotations

from chatchat.runtime.structured import mismatch
from chatchat.tool import Tool, tool

from .names import STRUCTURED_OUTPUT


@tool(
    name=STRUCTURED_OUTPUT,
    description="""Return your final answer as the structured payload below.
Call it exactly once, at the end of the work; nothing you say in prose counts as the answer.
The payload must match this schema.""",
    parameters=None,
)
async def structured_output(context, **input) -> str:
    team = getattr(context, "team", None)
    if team is None:
        return "Error: this session has no team to record the answer on."
    problem = mismatch(team.tools.output_schema or {}, input)
    if problem:
        return f"Error: output does not match the required schema: {problem}"
    team.tools.structured_output = dict(input)
    return "Structured output recorded. Finish the run now."


def tool_for(schema: dict) -> Tool:
    return structured_output.with_parameters(schema)
