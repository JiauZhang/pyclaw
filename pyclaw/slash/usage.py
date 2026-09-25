# Usage, cost and context commands: what the recorded work cost.
from pyclaw.tui.formatting import _plural


def _pricing() -> dict:
    from pyclaw.config import load as load_config
    return load_config().get('pricing') or {}

def _cost_of(session):
    from pyclaw.cost import usage_cost
    return usage_cost(session.model, session.usage, _pricing())

def _history_window(arg: str) -> tuple[int, str]:
    if str(arg or '').strip().lower() in ('day', 'today'):
        return 1, 'today'
    if str(arg or '').strip().lower() in ('week',):
        return 7, 'last 7 days'
    if str(arg or '').strip().isdigit():
        return int(arg), f'last {arg} days'
    return 1, 'today'

def _history_cost(rows: list, pricing) -> float | None:
    from pyclaw.cost import usage_cost

    total = 0.0
    priced = False
    for row in rows:
        cost = usage_cost(row.get('model'), row, pricing)
        if cost is not None:
            total += cost
            priced = True
    return total if priced else None

def _ms(ms: int) -> str:
    return f'{int(ms) / 1000:.1f}s'

def _usage_lines(session, arg: str) -> str:
    from pyclaw.cost import format_cost
    from pyclaw.tui.formatting import _format_count
    from pyclaw.usage_history import read_days, totals

    days, label = _history_window(arg)
    rows = read_days(days)
    if not rows:
        return f'Usage \u00b7 {label}\nNothing recorded yet.'
    seen = totals(rows)
    return '\n'.join([
        f'Usage \u00b7 {label}',
        (f'tokens {_format_count(seen["total"])} \u00b7 input: '
         f'{seen["input"]}  output: {seen["output"]}  cache read: '
         f'{seen["cached"]}'),
        (f'{_plural(seen["turns"], "turn")} \u00b7 '
         f'{_plural(seen["tool_calls"], "tool call")} \u00b7 '
         f'{_ms(seen["tool_ms"])} in tools \u00b7 '
         f'{_ms(seen["api_ms"])} with the model'),
        (f'{seen["lines_added"]} lines added \u00b7 '
         f'{seen["lines_removed"]} lines removed \u00b7 '
         f'{_plural(seen["hooks"], "hook run")} \u00b7 '
         f'{_plural(seen["denials"], "refused call")}'),
        f'cost: {format_cost(_history_cost(rows, _pricing()))} at your '
        f'configured rates'])

def _stats_lines(arg: str) -> str:
    from pyclaw.events import read_errors
    from pyclaw.usage_history import by_day, read_days

    days = int(arg) if str(arg or '').strip().isdigit() else 7
    rows = read_days(days)
    if not rows:
        return 'Stats\nNothing recorded yet.'
    lines = [f'Stats \u00b7 last {days} days']
    for day, seen in by_day(rows):
        lines.append(f'{day} \u00b7 in {seen["input"]} \u00b7 out '
                     f'{seen["output"]} \u00b7 {seen["tool_calls"]} tool '
                     f'calls \u00b7 {seen["api_ms"] / 1000:.1f}s with the '
                     f'model')
    errors = read_errors(days=days)
    if errors:
        lines.append(f'{_plural(len(errors), "error")} recorded: '
                     + '; '.join(str(row.get('text') or '')[:60]
                                 for row in errors[-3:]))
    return '\n'.join(lines)

async def _handle_model(session, arg: str) -> str:
    if not arg:
        return f'Model: {session.model}'
    session.set_model(arg)
    from pyclaw import load as load_config
    from pyclaw.config import save as save_config
    config = load_config()
    config['model'] = session.model
    save_config(config)
    await session.note_config_change('config')
    return f'Model: {session.model}'

def _agent_cost(model: str, usage) -> str:
    from pyclaw.cost import format_cost, usage_cost
    cost = usage_cost(model, usage, _pricing())
    price = format_cost(cost) + ('' if cost is not None else ' unpriced')
    return (f'{usage.prompt_tokens} in / {usage.completion_tokens} out'
            f' / {usage.total_tokens} tokens \u00b7 {price}')

def _handle_cost(session, arg: str) -> str:
    from pyclaw.cost import format_cost
    usage = session.usage
    cost = _cost_of(session)
    detail = format_cost(cost)
    if cost is None:
        detail += f' (add pricing.{session.model} to config)'
    lines = [f"Model: {session.model}",
             f"Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out"
             f" / {usage.total_tokens} total",
             f"Cost: {detail}"]
    agents = session.agent_usage()
    if len(agents) > 1:
        lines += ['', 'By agent:'] + [
            f'@{name} \u00b7 {model or session.model} \u00b7 '
            f'{_agent_cost(model or session.model, agent_usage)}'
            for name, model, agent_usage in agents]
    return '\n'.join(lines)

def _context(session, arg: str) -> str:
    import json

    def size(value) -> int:
        return len(str(value if value is not None else ''))

    window = int(session.context_window or 0)
    used = int(session.used_context)
    out = [f'Context for {session.model}']
    if window:
        out.append(f'Measured: {used:,} of {window:,} tokens '
                   f'({round(used / window * 100)}%) '
                   f'\u00b7 {max(0, window - used):,} free')
        out.append(f'Auto-compact at {int(session.compact_threshold):,} tokens')
    else:
        out.append(f'Measured: {used:,} tokens \u00b7 no window configured')

    tools = sorted(((str(schema.get('name', '')),
                     size(json.dumps(schema, sort_keys=True))
                     + size(schema.get('description', '')))
                    for schema in session.tool_schemas()),
                   key=lambda row: -row[1])
    memory = [str(item.get('path') or '') for item in session.instruction_files
              if isinstance(item, dict)]
    agents = list(session.agent_types)
    transcript = session.transcript()

    out += ['', 'What the model is sent, in characters:',
            f'  System prompt:  {size(session.lead_instruction):,}',
            f'  Memory files:   {len(memory)}']
    out += [f'    {path}' for path in memory if path]
    out.append(f'  Tools:          {len(tools)}')
    out += [f'    {name} {chars:,}' for name, chars in tools[:5]]
    out.append(f'  Agents:         {len(agents)}')
    out += [f'    {name}' for name, _ in agents]
    out.append(f'  Conversation:   {session.context_messages} messages '
               f'({sum(size(message.get("content")) for message in transcript):,}'
               f' chars)')
    return '\n'.join(out)
