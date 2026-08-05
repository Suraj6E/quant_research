"""The four data regimes, and the pre-registered rule they all execute.

Every variant runs the SAME rule. They differ only in what the simulation is
permitted to know, and each adds exactly one advantage that was unavailable in
reality:

    A -> B   the revised value instead of the first print
    B -> C   the release date instead of the release instant
    C -> D   a mid price instead of the prevailing bid/ask

The differences between adjacent variants are the estimand. The level of any one
of them is of little interest, and variant A is not expected to be attractive.

The dishonest variants are not strawmen. Each is a faithful reproduction of a
mistake that appears in real research, and the gap it opens against A is the
quantity being measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from fxpit.experiment.releases import Release

# Pre-registered and fixed. See docs/preregistration.md §4.
HOLD_MINUTES = 30

# How far past the entry instant to look for a tick before giving up on an
# event. An event with no tick is skipped for ALL variants, so the arms always
# compare the same event set.
ENTRY_TOLERANCE_MINUTES = 5


@dataclass(frozen=True)
class Variant:
    key: str
    name: str
    macro: str
    timing: str
    execution: str
    use_revised: bool
    date_only: bool
    use_mid: bool


VARIANTS = [
    Variant(
        key="A", name="Honest",
        macro="First print, as_of the release",
        timing="Release instant",
        execution="Real bid/ask",
        use_revised=False, date_only=False, use_mid=False,
    ),
    Variant(
        key="B", name="Revised values",
        macro="Final revised value",
        timing="Release instant",
        execution="Real bid/ask",
        use_revised=True, date_only=False, use_mid=False,
    ),
    Variant(
        key="C", name="Revised + date-only",
        macro="Final revised value",
        timing="Date only, entry at day open",
        execution="Real bid/ask",
        use_revised=True, date_only=True, use_mid=False,
    ),
    Variant(
        key="D", name="C plus mid-price",
        macro="Final revised value",
        timing="Date only, entry at day open",
        execution="Mid price, no spread",
        use_revised=True, date_only=True, use_mid=True,
    ),
]

BY_KEY = {v.key: v for v in VARIANTS}


def entry_instant(variant: Variant, release: Release) -> datetime:
    """When this variant is allowed to act.

    The date-only variants enter at 00:00 UTC on the release date — which is
    typically 13.5 hours BEFORE the release. That is the whole point: a
    date-only macro column cannot distinguish an 08:30 print from an 08:29
    price, so a simulation built on one can act on news it had not heard.
    """
    if variant.date_only:
        return datetime.combine(release.release_date, time(0, 0), tzinfo=UTC)
    return release.release_ts


def signal(variant: Variant, release: Release) -> int:
    """+1 long EURUSD, -1 short, 0 no trade.

    Stronger-than-trend US data implies a stronger dollar, so a positive
    surprise SELLS EURUSD. The revised variants recompute the surprise from the
    revised series, which is the contamination being measured rather than a bug.
    """
    surprise = release.surprise_final if variant.use_revised else release.surprise_first
    if surprise > 0:
        return -1
    if surprise < 0:
        return 1
    return 0


def fill_prices(variant: Variant, bid: float, ask: float, direction: int) -> float:
    """The price this variant pays to open, and receives to close.

    A buy enters at the ask and exits at the bid; a sell enters at the bid and
    exits at the ask. Variant D uses the mid on both sides, so its advantage is
    exactly one full spread per round trip — which is the quantity C -> D
    measures.
    """
    if variant.use_mid:
        return (bid + ask) / 2.0
    return ask if direction > 0 else bid


def exit_price(variant: Variant, bid: float, ask: float, direction: int) -> float:
    if variant.use_mid:
        return (bid + ask) / 2.0
    # Closing reverses the side.
    return bid if direction > 0 else ask


def hold_until(entry: datetime) -> datetime:
    return entry + timedelta(minutes=HOLD_MINUTES)
