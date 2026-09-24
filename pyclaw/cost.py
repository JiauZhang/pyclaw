from __future__ import annotations

_TOKENS_PER_UNIT = 1_000_000


def usage_cost(model, usage, pricing) -> float | None:
    rates = (pricing or {}).get(model)
    if not rates:
        return None
    as_row = isinstance(usage, dict)
    read = (lambda key, default=0: usage.get(key, default)) if as_row else (
        lambda key, default=0: getattr(usage, key, default))
    details = read('prompt_tokens_details') or (
        {'cached_tokens': read('cached')} if as_row else {})
    cached = details.get('cached_tokens', 0) or 0
    prompt = read('input') if as_row else read('prompt_tokens')
    output = read('output') if as_row else read('completion_tokens')
    uncached = max(prompt - cached, 0)
    total = (uncached * (rates.get('input') or 0)
             + cached * (rates.get('cache_read') or 0)
             + output * (rates.get('output') or 0))
    return total / _TOKENS_PER_UNIT


def format_cost(cost) -> str:
    if cost is None:
        return 'unpriced'
    if cost and cost < 0.01:
        return f'${cost:.4f}'
    return f'${cost:.2f}'
