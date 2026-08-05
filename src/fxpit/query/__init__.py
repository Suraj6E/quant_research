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

__all__ = ["Bar", "MacroFact", "Tick", "bars_as_of", "macro_as_of", "ticks_as_of"]
