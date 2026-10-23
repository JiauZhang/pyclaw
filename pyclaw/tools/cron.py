from __future__ import annotations

from chatchat.tool import tool

from pyclaw.cron_expr import human
from pyclaw.cron_schedule import CronStore

from .names import CRON_CREATE, CRON_DELETE, CRON_LIST

_store: CronStore | None = None


def set_store(store: CronStore | None) -> None:
    global _store
    _store = store


def store() -> CronStore:
    global _store
    if _store is None:
        raise RuntimeError("cron tools need a store: call set_store first")
    return _store


def _cron_where(task: dict) -> str:
    if task.get("durable") is False:
        return "Session-only (not written to disk, dies when pyclaw exits)"
    return "Persisted to scheduled_tasks.json"


def _cron_line(task: dict) -> str:
    kind = "recurring" if task.get("recurring") else "one-shot"
    marker = " [session-only]" if task.get("durable") is False else ""
    prompt = str(task.get("prompt") or "")
    if len(prompt) > 80:
        prompt = prompt[:80] + "…"
    return f"{task['id']} — {human(task['cron'])} ({kind}){marker}: {prompt}"


@tool(
    name=CRON_CREATE,
    description="""Schedule a future prompt: 5-field cron in local time (min hour day month weekday).
recurring: false fires once at the next match; pin min/hour/day/month, e.g. "30 14 27 5 *".
Avoid :00/:30 when the user is approximate: "around 9am" -> "57 8 * * *".
Recurring jobs expire in 7 days; durable: true persists, false (default) is session-only.
""",
    parameters={
        "type": "object",
        "properties": {
            "cron": {
                "type": "string",
                "description": 'Standard 5-field cron in local time: "minute hour day-of-month month day-of-week".',
            },
            "prompt": {"type": "string", "description": "The prompt to enqueue at each fire time."},
            "recurring": {
                "type": "boolean",
                "default": True,
                "description": "true (default) fires on every cron match until deleted or expired; false fires once at the next match.",
            },
            "durable": {
                "type": "boolean",
                "default": False,
                "description": "true persists the job to scheduled_tasks.json so it survives restarts; false (default) keeps it session-only.",
            },
        },
        "required": ["cron", "prompt"],
        "additionalProperties": False,
    },
)
def CronCreate(context, cron: str, prompt: str, recurring: bool | None = None, durable: bool | None = None) -> str:
    tasks = store()
    task = tasks.add(
        str(cron or ""),
        str(prompt or ""),
        recurring=recurring is not False,
        durable=bool(durable),
    )
    if task is None:
        return f"Error: {tasks.refused}"
    if task.get("recurring"):
        return (
            f"Scheduled recurring job {task['id']} "
            f"({human(task['cron'])}). {_cron_where(task)}. "
            f"Auto-expires after 7 days. Use CronDelete to cancel "
            f"sooner."
        )
    return (
        f"Scheduled one-shot task {task['id']} "
        f"({human(task['cron'])}). {_cron_where(task)}. "
        f"It will fire once then auto-delete."
    )


@tool(
    name=CRON_LIST,
    description="Show every scheduled prompt with its id, schedule and whether it is durable.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)
def CronList(context) -> str:
    tasks = store().all()
    if not tasks:
        return "Nothing scheduled."
    return "\n".join(_cron_line(task) for task in tasks)


@tool(
    name=CRON_DELETE,
    description="Cancel a scheduled prompt by its id.",
    parameters={
        "type": "object",
        "properties": {"id": {"type": "string", "description": "The job ID returned by CronCreate."}},
        "required": ["id"],
        "additionalProperties": False,
    },
)
def CronDelete(context, id: str) -> str:
    ident = str(id or "").strip()
    removed = store().remove(ident)
    if removed is None:
        return f'Error: nothing scheduled under "{ident}"'
    return f"{removed['id']} cancelled."
