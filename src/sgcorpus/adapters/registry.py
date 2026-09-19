"""Adapter registry.

Adding a source is a new module here plus a source note in docs/sources/.
Nothing else in the codebase changes.
"""

from __future__ import annotations

from .base import NotImplementedAdapter, SourceAdapter
from .elitigation import ElitigationAdapter
from .hansard import HansardAdapter
from .pdpc import PdpcAdapter
from .sso import SsoAdapter

_ADAPTERS: dict[str, SourceAdapter] = {
    "hansard": HansardAdapter(),
    "sso": SsoAdapter(),
    "elitigation": ElitigationAdapter(),
    "pdpc": PdpcAdapter(),
}


def get(name: str) -> SourceAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise KeyError(
            f"unknown adapter {name!r}; available: {', '.join(sorted(_ADAPTERS))}"
        ) from None


def all_adapters() -> dict[str, SourceAdapter]:
    return dict(_ADAPTERS)


def implemented() -> list[str]:
    return [
        name
        for name, adapter in _ADAPTERS.items()
        if not isinstance(adapter, NotImplementedAdapter)
    ]
