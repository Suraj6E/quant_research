"""Tier C — the point-in-time query layer.

This package is the ONLY sanctioned read path. Nothing outside it may query
Postgres or ClickHouse directly; convenience accessors that skip the
`known_at` filter are how look-ahead bias re-enters a system that had
eliminated it.
"""

from fxpit.query.as_of import (
    Bar,
    MacroFact,
    Tick,
    bars_as_of,
    macro_as_of,
    ticks_as_of,
)
from fxpit.query.session import (
    QuerySession,
    default_session,
    fixture_session,
    open_production_session,
    set_default_session,
)

__all__ = [
    "Bar",
    "MacroFact",
    "QuerySession",
    "Tick",
    "bars_as_of",
    "default_session",
    "fixture_session",
    "macro_as_of",
    "open_production_session",
    "set_default_session",
    "ticks_as_of",
]
