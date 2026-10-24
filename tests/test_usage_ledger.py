from pyclaw.session.usage_ledger import UsageLedger


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, cached=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = {"cached_tokens": cached}


def test_a_turn_records_what_it_spent_and_not_what_the_session_spent():
    ledger = UsageLedger()
    ledger.record_turn(
        session="s",
        provider="p",
        model="m",
        usage=_Usage(prompt_tokens=100, completion_tokens=20),
        metrics={"tool_calls": 1},
        turns=1,
        started=0,
    )
    entry = ledger.record_turn(
        session="s",
        provider="p",
        model="m",
        usage=_Usage(prompt_tokens=150, completion_tokens=30),
        metrics={"tool_calls": 2},
        turns=2,
        started=0,
    )
    assert entry["input"] == 50
    assert entry["output"] == 10
    assert entry["metrics"]["tool_calls"] == 1


def test_the_cached_tokens_of_a_turn_are_the_ones_since_the_last_one():
    ledger = UsageLedger()
    ledger.record_turn(
        session="s",
        provider="p",
        model="m",
        usage=_Usage(prompt_tokens=100, cached=10),
        metrics={},
        turns=1,
        started=0,
    )
    entry = ledger.record_turn(
        session="s",
        provider="p",
        model="m",
        usage=_Usage(prompt_tokens=200, cached=40),
        metrics={},
        turns=2,
        started=0,
    )
    assert entry["cached"] == 30


def test_resetting_starts_the_counting_at_zero_again():
    ledger = UsageLedger()
    ledger.record_turn(
        session="s",
        provider="p",
        model="m",
        usage=_Usage(prompt_tokens=100, completion_tokens=20),
        metrics={"tool_calls": 1},
        turns=1,
        started=0,
    )
    ledger.reset()
    assert ledger.seen["input"] == 0
    assert ledger.seen["output"] == 0
    assert ledger.seen["tool_calls"] == 0


def test_a_conversation_that_was_never_counted_carries_nothing():
    ledger = UsageLedger()
    assert ledger.carry("never-existed") == ledger.seen
