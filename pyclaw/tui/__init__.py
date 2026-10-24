from . import tool_cards as _tool_cards  # noqa: F401  registers the built-in tool cards


def __getattr__(name):
    if name == "PyClawApp":
        from pyclaw.tui.app import PyClawApp  # noqa: PLC0415

        return PyClawApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PyClawApp"]
