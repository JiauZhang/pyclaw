"""Plan mode: the model asks to design first, and the plan needs approval."""

import asyncio

from pyclaw.team.builder import build_team
from pyclaw.tools.plan import APPROVE, AUTO_ACCEPT, KEEP_PLANNING


def _team(tmp_path):
    async def build():
        return build_team("agnes/agnes-2.5-flash", cwd=str(tmp_path))

    team = asyncio.run(build())
    team.product.gate.plan_file = tmp_path / "plan.md"
    return team


def test_entering_plan_mode_switches_the_session(tmp_path):
    team = _team(tmp_path)

    async def main():
        outcome = await team.execute_tool("EnterPlanMode", {}, team.lead)
        return outcome.text, team.product.gate.mode.value

    text, mode = asyncio.run(main())
    assert mode == "plan"
    assert str(tmp_path / "plan.md") in text


def test_entering_twice_is_a_no_op(tmp_path):
    team = _team(tmp_path)

    async def main():
        await team.execute_tool("EnterPlanMode", {}, team.lead)
        outcome = await team.execute_tool("EnterPlanMode", {}, team.lead)
        return outcome.text, team.product.gate.mode.value

    text, mode = asyncio.run(main())
    assert "Already in plan mode" in text
    assert mode == "plan"


def test_a_plan_that_was_never_written_cannot_be_approved(tmp_path):
    team = _team(tmp_path)
    team.set_user_input(lambda agent, questions: [APPROVE])

    async def main():
        return await team.execute_tool("ExitPlanMode", {}, team.lead)

    outcome = asyncio.run(main())
    assert outcome.text.startswith("Error:")
    assert "plan.md" in outcome.text


def test_approving_the_plan_leaves_plan_mode_with_the_plan(tmp_path):
    team = _team(tmp_path)
    (tmp_path / "plan.md").write_text("1. rename the parser\n")
    team.product.gate.mode = team.product.gate.mode.plan
    team.set_user_input(lambda agent, questions: [APPROVE])

    async def main():
        outcome = await team.execute_tool("ExitPlanMode", {}, team.lead)
        return outcome.text, team.product.gate.mode.value

    text, mode = asyncio.run(main())
    assert mode == "default"
    assert "rename the parser" in text
    assert "approved" in text


def test_asking_for_auto_accept_answers_with_that_mode(tmp_path):
    team = _team(tmp_path)
    (tmp_path / "plan.md").write_text("the plan\n")
    team.product.gate.mode = team.product.gate.mode.plan
    team.set_user_input(lambda agent, questions: [AUTO_ACCEPT])

    async def main():
        outcome = await team.execute_tool("ExitPlanMode", {}, team.lead)
        return outcome.text, team.product.gate.mode.value

    text, mode = asyncio.run(main())
    assert mode == "acceptEdits"
    assert "the plan" in text


def test_keeping_planning_leaves_the_session_in_plan_mode(tmp_path):
    team = _team(tmp_path)
    (tmp_path / "plan.md").write_text("the plan\n")
    team.product.gate.mode = team.product.gate.mode.plan
    team.set_user_input(lambda agent, questions: [KEEP_PLANNING])

    async def main():
        outcome = await team.execute_tool("ExitPlanMode", {}, team.lead)
        return outcome.text, team.product.gate.mode.value

    text, mode = asyncio.run(main())
    assert mode == "plan"
    assert "did not approve" in text


def test_the_plan_question_offers_the_three_ways_out(tmp_path):
    team = _team(tmp_path)
    (tmp_path / "plan.md").write_text("the plan\n")
    team.product.gate.mode = team.product.gate.mode.plan
    asked = []

    def asker(agent, questions):
        asked.extend(questions)
        return [APPROVE]

    team.set_user_input(asker)

    async def main():
        await team.execute_tool("ExitPlanMode", {}, team.lead)
        return asked

    asked = asyncio.run(main())
    labels = [option["label"] for option in asked[0]["options"]]
    assert labels == [APPROVE, AUTO_ACCEPT, KEEP_PLANNING]
