"""A question the model asks has to reach a human and come back as answers."""

import asyncio

from pyclaw.team.builder import build_team


def _question(header="shape", **kw):
    return dict(
        {
            "question": "Which shape?",
            "header": header,
            "options": [{"label": "round", "description": "a circle"}, {"label": "square", "description": "a box"}],
        },
        **kw,
    )


def _ask(tmp_path, questions, asker=None, seen=None):
    async def main():
        team = build_team("agnes/agnes-2.5-flash", cwd=str(tmp_path))
        if asker is not None:
            team.set_user_input(asker)
        if seen is not None:
            team.hooks.register("Elicitation", "*", fn=lambda inp: seen.append(("ask", inp.get("message"))) or True)
            team.hooks.register("ElicitationResult", "*", fn=lambda inp: seen.append(("result", inp.get("response"))))
        return await team.execute_tool("AskUserQuestion", {"questions": questions}, team.lead)

    return asyncio.run(main())


def test_the_answers_come_back_with_the_question(tmp_path):
    outcome = _ask(tmp_path, [_question()], asker=lambda agent, questions: ["round"])
    assert "Which shape?" in outcome.text
    assert "round" in outcome.text


def test_the_asker_sees_every_question_it_is_asked(tmp_path):
    asked = []

    def answer(agent, questions):
        asked.append(questions)
        return ["round, square"]

    outcome = _ask(tmp_path, [_question(), _question(multiSelect=True)], asker=answer)
    assert asked[0][1]["multiSelect"] is True
    assert "round, square" in outcome.text


def test_both_ends_of_the_round_trip_are_reported_to_hooks(tmp_path):
    seen = []
    _ask(tmp_path, [_question()], asker=lambda agent, questions: ["round"], seen=seen)
    assert ("ask", "Which shape?") in seen
    assert ("result", "Which shape?: round") in seen


def test_a_question_is_refused_when_no_one_can_answer_it(tmp_path):
    outcome = _ask(tmp_path, [_question()])
    assert "no one can answer" in outcome.text


def test_malformed_questions_are_refused_before_anyone_is_bothered(tmp_path):
    asked = []
    bad = (
        [],
        [{"options": [{"label": "x"}, {"label": "y"}]}, _question()],
        [_question(options=[{"label": "only one", "description": "no choice at all"}])],
    )
    for questions in bad:
        outcome = _ask(tmp_path, questions, asker=lambda agent, q: asked.append(q) or ["x"])
        assert outcome.text.startswith("Error:"), questions
    assert asked == []


def test_a_header_stays_short_enough_to_be_a_tab(tmp_path):
    outcome = _ask(tmp_path, [_question("a-header-too-long-for-the-tab")], asker=lambda agent, questions: ["round"])
    assert outcome.text.startswith("Error:") and "header" in outcome.text


def test_an_option_can_carry_the_artifact_it_would_produce(tmp_path):
    async def main():
        team = build_team("agnes/agnes-2.5-flash", cwd=str(tmp_path))
        team.set_user_input(lambda agent, questions: [])
        schema = next(s for s in team.tool_schemas(team.tool_context) if s["name"] == "AskUserQuestion")
        return schema["input_schema"]["properties"]["questions"]["items"]["properties"]["options"]["items"]["properties"]

    assert "preview" in asyncio.run(main())
