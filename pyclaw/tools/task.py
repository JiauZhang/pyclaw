from chatchat.tool import tool

from ..task import schedule, cancel, list_tasks


@tool(
    name='send_message_later',
    description='Send a text message to the current user after a delay in this process. Supports delay like "30s", "5m", "2h", an absolute time today like "17:00:00", or plain seconds. Use this for reminders and delayed notifications, NOT for tasks to run a shell command.',

    parameters={
        'type': 'object',
        'properties': {
            'message': {'type': 'string', 'description': 'text content to deliver to the user'},
            'when': {'type': 'string', 'description': 'e.g. "30s", "5m", "2h", "17:00:00", or seconds'},
        },
        'required': ['message', 'when'],
    },
)
async def send_message_later(ctx, message: str, when: str) -> str:
    from ..agents import session_of
    session = session_of(ctx.agent)
    if session is None:
        return 'Error: no active session to deliver to.'
    return session.schedule_delivery(message, when)


@tool(
    name='schedule_task',
    description='Schedule a shell command to run later in this process. Supports delay like "30s", "5m", "2h", an absolute time today like "17:00:00", or plain seconds. Optionally repeat. Returns the task id and next run time.',
    parameters={
        'type': 'object',
        'properties': {
            'command': {'type': 'string', 'description': 'shell command to run'},
            'when': {'type': 'string', 'description': 'e.g. "30s", "5m", "2h", "17:00:00", or seconds'},
            'repeat': {'type': 'boolean', 'description': 'repeat every interval', 'default': False},
        },
        'required': ['command', 'when'],
    },
)
async def schedule_task(command: str, when: str, repeat: bool = False) -> str:
    job = schedule(command, when, repeat)
    interval = f' every {job["delay"]:g}s' if repeat else ''
    at = job['next'].strftime('%Y-%m-%d %H:%M:%S')
    return f"Scheduled {job['id']} to run at {at}{interval}: {command}"


@tool(
    name='list_tasks',
    description='List currently scheduled tasks with ids, commands and next run times.',
    parameters={'type': 'object', 'properties': {}, 'required': []},
)
async def list_tasks_tool() -> str:
    jobs = list_tasks()
    if not jobs:
        return 'No scheduled tasks.'
    lines = [f"{j['id']} | next={j['next'].strftime('%H:%M:%S')} | repeat={j['repeat']} | {j['command']}" for j in jobs]
    return 'Scheduled tasks:\n' + '\n'.join(lines)


@tool(
    name='cancel_task',
    description='Cancel a scheduled task by its id.',
    parameters={
        'type': 'object',
        'properties': {'task_id': {'type': 'string', 'description': 'task id returned by schedule_task'}},
        'required': ['task_id'],
    },
)
async def cancel_task_tool(task_id: str) -> str:
    return f"Cancelled {task_id}." if cancel(task_id) else f"Task not found: {task_id}"