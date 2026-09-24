def __getattr__(name):
    if name == "PyClawApp":
        from pyclaw.tui.app import PyClawApp
        return PyClawApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PyClawApp"]
