from __future__ import annotations

import os

from chatchat.tool import tool

from pyclaw.team.worktrees import (
    changes,
    create,
    create_by_hook,
    name_for,
    remove,
    repository_root,
)

from .names import ENTER_WORKTREE, EXIT_WORKTREE

_records: dict[str, dict] = {}


def _team_of(context):
    return getattr(context, "team", None)


def _plural_work(count: int, noun: str, qualifier: str = "") -> str:
    return f"{count} {qualifier}{noun}" + ("" if count == 1 else "s")


def _worktree_name(team) -> str:
    root = repository_root(team.tool_context.cwd) or team.tool_context.cwd
    return name_for(team.lead_session_id or team.name, root)


async def _hooked_path(team, name: str) -> str:
    result = await team.hooks.execute_worktree_create_hooks(team.lead, name=name)
    for hook in result.results:
        if hook.outcome != "success":
            continue
        if hook.worktree_path.strip():
            return hook.worktree_path.strip()
        if hook.stdout.strip():
            return hook.stdout.strip()
    failed = "; ".join((hook.message or "no output") for hook in result.results if hook.outcome != "success")
    raise ValueError("the WorktreeCreate hook gave no directory" + (f": {failed}" if failed else ""))


async def enter(team, name: str = "") -> str:
    if getattr(team, "worktree", None) is not None:
        raise ValueError(f"already working in {team.worktree['name']}")
    name = str(name or "").strip() or _worktree_name(team)
    try:
        if team.hooks.has("WorktreeCreate"):
            record = create_by_hook(team.tool_context.cwd, name, await _hooked_path(team, name))
        elif repository_root(team.tool_context.cwd):
            record = create(team.tool_context.cwd, name)
        else:
            return "Error: this directory is not a git repository and no WorktreeCreate hook is configured."
    except ValueError as exc:
        return f"Error: {exc}"
    team.worktree = record
    team.set_cwd(record["path"])
    await team.hooks.execute_cwd_changed_hooks(team.lead, str(record["path"]))
    where = f"{record['path']} ({record['name']})"
    if record.get("hook_based"):
        return f"Working in {where}, set up by the WorktreeCreate hook."
    if record.get("resumed"):
        return f"Back into the worktree at {where}, on branch {record['branch']}."
    return f"Working in {where} on branch {record['branch']}. The session directory moved with it."


async def leave(team, keep: bool = False, discard: bool = False) -> str:
    record = getattr(team, "worktree", None)
    if record is None:
        return "Error: this session never entered a worktree, so there is nothing to exit and nothing was changed."
    counts = None if keep else changes(record["path"], record.get("origin_head") or "")
    if not keep and not discard:
        if counts is None:
            return (
                f"Error: git could not say what is in the worktree at {record['path']}. "
                "Refusing to remove it on that guess — answer again having confirmed the work is not needed, or keep it."
            )
        if counts["files"] or counts["commits"]:
            left = []
            if counts["files"]:
                left.append(_plural_work(counts["files"], "file", "uncommitted "))
            if counts["commits"]:
                left.append(_plural_work(counts["commits"], "commit"))
            return (
                "Error: this worktree has " + " and ".join(left) + ". Removing it throws that work away for good. "
                "Check with the user, then answer again wanting to discard, or keep the worktree."
            )
    team.worktree = None
    team.set_cwd(record["origin"])
    await team.hooks.execute_cwd_changed_hooks(team.lead, str(record["origin"]))
    kept = f"Back at {record['origin']}."
    if keep:
        return (
            kept
            + f" The work is untouched at {record['path']}"
            + (f" on branch {record['branch']}." if record["branch"] else ".")
        )
    if record.get("hook_based"):
        await team.hooks.execute_worktree_remove_hooks(team.lead, worktree_path=str(record["path"]))
        return kept + f" The WorktreeRemove hook was told about {record['path']}."
    discarded = []
    if counts and counts["commits"]:
        discarded.append(_plural_work(counts["commits"], "commit"))
    if counts and counts["files"]:
        discarded.append(_plural_work(counts["files"], "file", "uncommitted "))
    try:
        remove(record)
    except ValueError as exc:
        return kept + f" The worktree could not be removed: {exc}"
    return kept + " The worktree was removed." + (f" Discarded {' and '.join(discarded)}." if discarded else "")


async def isolate(name: str) -> str:
    record = create(os.getcwd(), name)
    _records[str(record["path"])] = record
    return str(record["path"])


async def release(path: str) -> str:
    record = _records.pop(str(path), None)
    if record is None:
        return ""
    counts = changes(record["path"], record.get("origin_head") or "")
    if counts is not None and not counts["files"] and not counts["commits"]:
        try:
            remove(record)
        except (OSError, ValueError) as exc:
            return f"Its worktree {path} could not be removed: {exc}"
        return ""
    return f"Its work stays in the worktree {path} on branch {record['branch']}."


@tool(
    name=ENTER_WORKTREE,
    description="""Only when the user asks for a worktree: make an isolated git worktree under .pyclaw/worktrees
and move this session into it, so the work cannot touch the checked-out directory.
Needs a git repository or WorktreeCreate/WorktreeRemove hooks, and must not already be in one.""",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": """Optional name for the worktree: each "/"-separated segment takes only letters, digits,
dots, dashes and underscores, up to 64 characters in all. A readable name is generated when it is left out.""",
            }
        },
        "additionalProperties": False,
    },
)
async def EnterWorktree(context, name: str = "") -> str:
    team = _team_of(context)
    if team is None:
        return "Error: EnterWorktree needs a session; this context has no team."
    return await enter(team, name)


@tool(
    name=EXIT_WORKTREE,
    description="""Leave the worktree this session moved into, back to the original directory.
Only worktrees this session entered: one made by hand or in another conversation is left alone.""",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["keep", "remove"],
                "description": '"keep" leaves the worktree directory and its branch on disk; "remove" deletes both.',
            },
            "discard_changes": {
                "type": "boolean",
                "description": """Only with "remove": throw away the uncommitted files and the commits no other branch has.
The tool refuses and lists them otherwise, so ask the user first.""",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
async def ExitWorktree(context, action: str, discard_changes: bool = False) -> str:
    team = _team_of(context)
    if team is None:
        return "Error: ExitWorktree needs a session; this context has no team."
    return await leave(team, keep=str(action or "") == "keep", discard=bool(discard_changes))
