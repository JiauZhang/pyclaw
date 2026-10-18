from chatchat.runtime.thinking import LEVELS, Thinking

from pyclaw import config as config_module

# Session setup commands: how much the model may reason, and the status line.
EFFORT_HINT = "effort takes one of: " + ", ".join(LEVELS)


async def _handle_effort(session, arg: str) -> str:
    arg = arg.strip().lower()
    if not arg:
        return session.thinking.label()
    if arg not in LEVELS:
        return EFFORT_HINT
    session.set_thinking(Thinking(arg))
    session.remember_thinking()
    await session.note_config_change("settings")
    return session.thinking.label()


STATUSLINE_PROMPT = "Set up my status line from my shell PS1 configuration"


def _handle_statusline(session, arg: str) -> tuple:
    """The setup agent reads shell config anywhere under home and writes one
    settings file, so those are let through for the rest of this session."""
    prompt = arg or STATUSLINE_PROMPT
    session.grant_tools(("Read(~/**)", f"Edit({config_module.__config_file__})"))
    return (
        "Setting up the status line…",
        f'Create an agent with the Agent tool, subagent_type "statusline-setup" and the prompt "{prompt}"',
    )
