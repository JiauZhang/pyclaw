from __future__ import annotations

SOURCES = {
    "userSettings": "user",
    "projectSettings": "project",
    "localSettings": "local",
    "session": "session",
    "cliArg": "cli",
}

MODES = {
    "default": "default",
    "acceptEdits": "acceptEdits",
    "plan": "plan",
    "bypassPermissions": "allowAll",
}


def rule_value(rule: str) -> dict:
    name, opened, content = str(rule).partition("(")
    if not opened or not content.endswith(")"):
        return {"toolName": name}
    return {"toolName": name, "ruleContent": content[:-1]}


def rule_string(value: dict) -> str:
    content = value.get("ruleContent")
    return f"{value['toolName']}({content})" if content is not None else str(value["toolName"])


def suggested_updates(gate, tool_name: str, tool_input) -> list[dict]:
    rules = gate.suggested_rules(tool_name, tool_input)
    if not rules:
        return []
    return [
        {
            "type": "addRules",
            "rules": [rule_value(rule) for rule in rules],
            "behavior": "allow",
            "destination": "localSettings",
        }
    ]
