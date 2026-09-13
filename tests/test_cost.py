from pyclaw.cost import format_cost, usage_cost


class _Usage:
    def __init__(self, prompt=0, completion=0, total=0, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        self.prompt_tokens_details = {"cached_tokens": cached} if cached else None


def test_usage_cost_without_pricing_is_unknown():
    assert usage_cost("m", _Usage(10, 10, 20), {}) is None
    assert usage_cost("m", _Usage(10, 10, 20), None) is None


def test_usage_cost_sums_input_output_and_cache():
    usage = _Usage(prompt=2000, completion=1000, total=3000, cached=500)
    pricing = {"m": {"input": 3, "output": 15, "cache_read": 0.3}}
    cost = usage_cost("m", usage, pricing)
    expected = (1500 * 3 + 500 * 0.3 + 1000 * 15) / 1_000_000
    assert round(cost, 9) == round(expected, 9)


def test_format_cost_hides_noise_for_small_amounts():
    assert format_cost(0.002) == "$0.0020"
    assert format_cost(1.5) == "$1.50"
    assert format_cost(0) == "$0.00"
    assert format_cost(None) == "unpriced"
