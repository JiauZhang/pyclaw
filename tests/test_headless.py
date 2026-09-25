import asyncio
import json
from chatchat.runtime.thinking import Thinking


from pyclaw import __main__


def _fake_team():
    class T:
        provider = "p"
        model = "m"
        thinking = Thinking('off')
        name = "t"
        provided_tools = []

        def tool_schemas(self):
            return [{"name": "k", "description": "d", "input_schema": {}}]

        def transcript(self):
            return [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]

    return T()


class _U:
    def to_dict(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class _FakeSession:
    name = "t"
    conv_session_id = "hl"
    mode = "team"
    permission_mode = "default"
    provider = "p"
    model = "m"
    thinking = Thinking('off')
    usage = _U()

    def __init__(self, team, session_id=None, resume_from=None):
        self.team = team
        self.session_id = session_id
        self.resume_from = resume_from
        self.restored = False

    def restore_transcript(self):
        self.restored = True
        return 0

    async def chat(self, message, on_event=None):
        return "\n\nhello\n"

    def record_turn(self):
        return {'at': '', 'day': '', 'metrics': {}}

    async def close(self):
        pass


def test_prompt_once_returns_original_text(monkeypatch):
    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: _fake_team())
    monkeypatch.setattr(__main__, "Session", _FakeSession)
    out = asyncio.run(__main__.prompt_once("p", "m", "hi"))
    assert out["text"] == "\n\nhello\n"
    assert out["mode"] == "team"
    assert out["messages"] == 2
    assert out["usage"]["total_tokens"] == 0


def test_render_output_prints_the_requested_format(capsys):
    payload = {"text": "ok", "mode": "team", "messages": 2}
    __main__.render_output("text", payload)
    assert capsys.readouterr().out == "ok\n"
    __main__.render_output("json", payload)
    assert json.loads(capsys.readouterr().out) == payload

SCHEMA = {'type': 'object', 'properties': {'name': {'type': 'string'}},
          'required': ['name']}


class _ShapeTeam:
    provider = "p"
    model = "m"
    thinking = Thinking('off')
    name = "t"
    provided_tools = []

    def __init__(self):
        self.structured_output = None
        self.installed = []

    def set_output_schema(self, schema):
        self.installed.append(schema)
        if schema.get('type') == 'not-a-type':
            return "'not-a-type' is not recognized"
        return ''

    def tool_schemas(self):
        return []

    def transcript(self):
        return []


def test_print_mode_installs_the_shape_before_asking(monkeypatch):
    team = _ShapeTeam()
    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: team)
    monkeypatch.setattr(__main__, "Session", _FakeSession)
    out = asyncio.run(__main__.prompt_once("p", "m", "hi",
                                           json_schema=SCHEMA))
    assert team.installed == [SCHEMA]
    assert out["structured_output"] is None


def test_a_shape_that_is_not_a_shape_stops_the_run(monkeypatch):
    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: _ShapeTeam())
    monkeypatch.setattr(__main__, "Session", _FakeSession)
    try:
        asyncio.run(__main__.prompt_once("p", "m", "hi",
                                         json_schema={'type': 'not-a-type'}))
    except ValueError as exc:
        assert 'not-a-type' in str(exc)
    else:
        raise AssertionError('the run went ahead with a broken schema')


def test_the_payload_is_what_print_mode_hands_over(capsys, monkeypatch):
    class _AnsweringSession(_FakeSession):
        async def chat(self, message, on_event=None):
            self.team.structured_output = {'name': 'round'}
            return "\n\nprose\n"

    team = _ShapeTeam()
    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: team)
    monkeypatch.setattr(__main__, "Session", _AnsweringSession)
    out = asyncio.run(__main__.prompt_once("p", "m", "hi",
                                           json_schema=SCHEMA))
    assert out["structured_output"] == {'name': 'round'}
    __main__.render_output("text", out)
    assert json.loads(capsys.readouterr().out) == {'name': 'round'}
    __main__.render_output("json", out)
    assert json.loads(capsys.readouterr().out)["structured_output"] == \
        {'name': 'round'}


def test_print_mode_refuses_a_shape_that_is_not_json(capsys):
    args = __main__._build_parser().parse_args(['-p', 'hi', '--json-schema',
                                                '{oops'])
    try:
        __main__._json_schema(args)
    except SystemExit:
        assert 'not valid JSON' in capsys.readouterr().out
    else:
        raise AssertionError('a broken schema was accepted')


def test_a_shape_needs_a_prompt_to_shape(capsys):
    args = __main__._build_parser().parse_args(['--json-schema', '{}'])
    try:
        __main__._json_schema(args)
    except SystemExit:
        assert '--json-schema needs --print' in capsys.readouterr().out
    else:
        raise AssertionError('the shape reached an interactive run')


def test_the_shape_arrives_as_the_text_the_flag_carried():
    args = __main__._build_parser().parse_args(['-p', 'hi', '--json-schema',
                                                '{"type":"object"}'])
    assert __main__._json_schema(args) == {'type': 'object'}
    assert __main__._json_schema(
        __main__._build_parser().parse_args(['-p', 'hi'])) is None


def test_a_print_run_records_what_the_turn_used(monkeypatch):
    from pyclaw import usage_history

    recorded = []

    class _RecordingSession(_FakeSession):
        def record_turn(self):
            recorded.append(self.team.name)
            return {'at': '', 'day': ''}

    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: _ShapeTeam())
    monkeypatch.setattr(__main__, "Session", _RecordingSession)
    asyncio.run(__main__.prompt_once("p", "m", "hi"))

    assert recorded == ["t"]


def test_a_print_run_records_its_events_locally(monkeypatch, tmp_path):
    from chatchat.hooks.events import AGENT_TEXT, emit

    from pyclaw import events

    home = tmp_path / 'events'
    monkeypatch.setattr(events, '_directory', lambda: home)

    class _EmittingSession(_FakeSession):
        async def chat(self, message, on_event=None):
            emit(AGENT_TEXT, agent='lead', delta='hello')
            return "\n\nhello\n"

    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: _ShapeTeam())
    monkeypatch.setattr(__main__, "Session", _EmittingSession)
    asyncio.run(__main__.prompt_once("p", "m", "hi"))

    rows = [json.loads(line)
            for path in home.glob('*.jsonl')
            for line in path.read_text(encoding='utf-8').splitlines()]
    assert [row['kind'] for row in rows] == ['text']
    assert rows[0]['text'] == 'hello' and rows[0]['agent'] == 'lead'
