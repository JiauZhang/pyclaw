from __future__ import annotations

from chatchat.hooks.events import clear_runtime_sinks


def take_over_output() -> None:
    """PyClaw prints its own events, so chatchat's default printer goes.

    This used to happen while importing the package, which meant a library
    import changed global behaviour behind the importer's back. It is a step of
    building the application now, and building a team is where that begins.
    """
    clear_runtime_sinks()
