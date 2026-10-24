"""Every tool schema the model is offered has to be one it can fill in."""

import asyncio
import tempfile

from chatchat.tools import builtin_tools

from pyclaw.team.builder import build_team
from pyclaw.tools import BUILTIN_TOOLS


def _schema_problems(schema, where, problems):
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if kind != "object":
            problems.append(f"{where}: an object schema must say type=object")
        if "additionalProperties" not in schema:
            problems.append(f"{where}: extra keys are neither allowed nor refused")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for name in schema.get("required") or []:
            if name not in properties:
                problems.append(f"{where}: required {name} is not a property")
        if properties and schema.get("additionalProperties") is not False:
            problems.append(f"{where}: unknown keys are not refused")
        for name, child in properties.items():
            _schema_problems(child, f"{where}.{name}", problems)
        return
    if not str(schema.get("description") or "").strip():
        problems.append(f"{where}: no description")
    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            problems.append(f"{where}: array without items")
            return
        _schema_problems(items, f"{where}[]", problems)


def _tool_problems(schemas):
    problems = []
    for schema in schemas:
        if not str(schema.get("description") or "").strip():
            problems.append(f"{schema['name']}: no description")
        input_schema = schema.get("input_schema")
        if not isinstance(input_schema, dict):
            problems.append(f"{schema['name']}: no input_schema")
            continue
        _schema_problems(input_schema, f"{schema['name']}.input_schema", problems)
    return problems


def _schema_of(tool, context=None):
    description = tool.describe(context) if context is not None else tool.description
    return [{"name": tool.name, "description": description, "input_schema": tool.parameters}]


def _team_schemas(**kw):
    async def main():
        with tempfile.TemporaryDirectory() as directory:
            team = build_team("agnes/agnes-2.5-flash", cwd=directory, **kw)
            owned = [schema for tool in builtin_tools(team) for schema in _schema_of(tool, team.tool_context)]
            return owned, [schema["name"] for schema in team.tool_schemas(team.tool_context)]

    return asyncio.run(main())


def test_every_builtin_tool_schema_is_fillable():
    schemas = [schema for tool in BUILTIN_TOOLS for schema in _schema_of(tool)]
    assert len(schemas) == len(BUILTIN_TOOLS)
    assert _tool_problems(schemas) == []


def test_the_schemas_pyclaw_owns_are_fillable_in_both_modes():
    for kw in ({}, {"use_team": True}):
        owned, _names = _team_schemas(**kw)
        assert _tool_problems(owned) == []


def test_a_name_shared_by_a_team_tool_and_a_builtin_is_offered_once():
    _owned, names = _team_schemas(use_team=True)
    assert len(names) == len(set(names))
    for name in ("TaskOutput", "TaskStop"):
        assert names.count(name) == 1


def test_a_file_path_is_the_first_thing_a_reading_tool_asks_for():
    read = next(tool for tool in BUILTIN_TOOLS if tool.name == "Read")
    assert read.parameters["required"] == ["file_path"]
    assert "1-based" in read.parameters["properties"]["offset"]["description"]


def test_a_background_read_says_how_long_it_waits():
    output = next(tool for tool in BUILTIN_TOOLS if tool.name == "TaskOutput")
    properties = output.parameters["properties"]
    assert properties["block"]["default"] is True
    assert properties["timeout"]["default"] == 30_000
    assert properties["timeout"]["maximum"] == 600_000
