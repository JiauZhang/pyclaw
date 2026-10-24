from __future__ import annotations

from importlib import import_module

from .version import __version__

__all__ = [
    "__version__",
    "GatewayConfig",
    "GatewayServer",
    "IMChannelAdapter",
    "IM_EXTRA",
    "Session",
    "build_team",
    "load",
]

_LAZY = {
    "GatewayConfig": ("pyclaw.gateway", "GatewayConfig"),
    "GatewayServer": ("pyclaw.gateway", "GatewayServer"),
    "IMChannelAdapter": ("pyclaw.channels", "IMChannelAdapter"),
    "IM_EXTRA": ("pyclaw.team.builder", "IM_EXTRA"),
    "Session": ("pyclaw.session", "Session"),
    "build_team": ("pyclaw.team.builder", "build_team"),
    "load": ("pyclaw.config", "load"),
}


def __getattr__(name: str):
    """The application-facing names load on first use instead of at import.

    Importing this package used to pull in the gateway, the session, the TUI
    formatting and textual itself, so no tool could be used, and no test could
    run, without the whole application behind it.
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = target
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
