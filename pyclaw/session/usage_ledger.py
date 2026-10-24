"""What a session has already spent, and what it spent since.

The daily rows record what each turn cost, but a turn's cost is only the
part that came after the last one: the tokens, the cached tokens, the
seconds and the metrics are all counted from where the session last stood.
That place is the baseline, and keeping it is this ledger's job.

A resumed conversation has to carry on counting from where it left off, so
the baseline can also be read back from the rows that were written before.
"""

import datetime
import time

from chatchat.runtime.metrics import Metrics
from pyclaw.usage_history import conversation_totals, record, row


class UsageLedger:
    """The baseline a session's next turn is counted from."""

    def __init__(self):
        self._seen = self._zero()

    @staticmethod
    def _zero() -> dict:
        return {"input": 0, "output": 0, "cached": 0, "seconds": 0, **{name: 0 for name in Metrics().as_dict()}}

    @property
    def seen(self) -> dict:
        return dict(self._seen)

    def reset(self):
        """Start counting from nothing again."""
        self._seen = self._zero()

    def record_turn(self, *, session, provider, model, usage, metrics, turns, started) -> dict:
        """Write down what this turn cost, and move the baseline along.

        The row holds what the turn spent, not what the session has spent
        in all: the difference between the two is what a turn is.
        """
        details = getattr(usage, "prompt_tokens_details", None) or {}
        seen = self._seen
        entry = row(
            datetime.datetime.now().astimezone(),
            str(session),
            provider,
            model,
            input_tokens=usage.prompt_tokens - seen["input"],
            output_tokens=usage.completion_tokens - seen["output"],
            cached=(int(details.get("cached_tokens") or 0) - seen["cached"]),
            turns=turns,
            seconds=int(time.monotonic() - started) - seen["seconds"],
            metrics={name: value - seen[name] for name, value in metrics.items()},
        )
        self._seen = {
            "input": usage.prompt_tokens,
            "output": usage.completion_tokens,
            "cached": int(details.get("cached_tokens") or 0),
            "seconds": int(time.monotonic() - started),
            **metrics,
        }
        record(entry)
        return entry

    def carry(self, source_id, *, ledger=None) -> dict:
        """Carry on counting from where the conversation being continued had
        got to, so the cost and the status row do not fall back to zero the
        moment it is resumed.

        The team's own ledger is given the same sums, so the numbers it
        reports are the ones the conversation really spent.
        """
        seen = conversation_totals(str(source_id))
        spent = seen["input"] or seen["output"] or seen["seconds"] or any(seen["metrics"].values())
        if not spent:
            return self._seen
        if ledger is not None:
            ledger.restore(
                {
                    "prompt_tokens": seen["input"],
                    "completion_tokens": seen["output"],
                    "total_tokens": seen["total"],
                    "prompt_tokens_details": {"cached_tokens": seen["cached"]},
                },
                seen["metrics"],
            )
        self._seen = {
            "input": seen["input"],
            "output": seen["output"],
            "cached": seen["cached"],
            "seconds": seen["seconds"],
            **seen["metrics"],
        }
        return self._seen
