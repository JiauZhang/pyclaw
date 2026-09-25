from chatchat.core.thinking import Thinking

# Session setup commands: how much the model may reason, and the status line.
EFFORT_HINT = ('effort takes low, medium, high, or auto to leave it to the '
               'model')

def _reasoning(session, mode=None, budget=None, effort=None):

    current = session.thinking
    try:
        setting = Thinking(
            mode=current.mode if mode is None else mode,
            budget=current.budget if budget is None else budget,
            effort=current.effort if effort is None else effort)
    except ValueError:
        return None
    session.set_thinking(setting)
    return setting

async def _handle_effort(session, arg: str) -> str:
    arg = arg.strip()
    if not arg:
        return (f'effort {session.thinking.effort}'
                if session.thinking.effort else
                'effort auto (the model decides)')
    setting = _reasoning(session, effort='' if arg == 'auto' else arg)
    if setting is None:
        return EFFORT_HINT
    session.remember_thinking()
    await session.note_config_change('settings')
    return f'Effort: {session.thinking.label()}'

STATUSLINE_PROMPT = 'Set up my status line from my shell PS1 configuration'

def _handle_statusline(arg: str) -> tuple:
    prompt = arg or STATUSLINE_PROMPT
    return ('Setting up the status line…',
            f'Create an agent with the Agent tool, subagent_type '
            f'"statusline-setup" and the prompt "{prompt}"')
