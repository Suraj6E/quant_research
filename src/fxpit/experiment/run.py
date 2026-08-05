"""Execute the four variants and measure the differences between them.

Prices come from `tick_raw` through a windowed read. Note that this is the one
place in the project where a strategy exists at all, and it exists solely as a
measuring instrument — planning.md permits strategy code in Phase 6 and nowhere
else, and this rule is deliberately dull so that no result here reads as a
trading claim.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fxpit.config import ROOT
from fxpit.experiment import variants as V
from fxpit.experiment.releases import Release
from fxpit.ingest import store as ch_store

PREREG = ROOT / "docs" / "preregistration.md"

# Pre-registered: 12 payrolls + 12 CPI a year.
EVENTS_PER_YEAR = 24

# Below this many trades the Sharpe ratios are arithmetic, not evidence.
#
# There is no bright line where a sample becomes adequate, and 20 is a
# judgement rather than a theorem. It exists so that "too few events" is a
# STRUCTURAL property of the result object rather than a caveat somebody has to
# remember to write in prose — the failure mode this whole project studies is
# a number presented with more confidence than it can carry.
MIN_EVENTS_FOR_INTERPRETATION = 20


def preregistration_hash() -> str:
    """A digest of the pre-registration document, recorded with every result.

    The point of pre-registering is that the rule was fixed before the numbers
    were seen. That claim is only checkable if the document can be shown to be
    the one the run used, so the hash travels with the output.
    """
    if not PREREG.exists():
        return "MISSING"
    return hashlib.sha256(PREREG.read_bytes()).hexdigest()[:16]


@dataclass
class Trade:
    release: Release
    direction: int
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float

    @property
    def return_bps(self) -> float:
        """Signed return in basis points, net of the spread paid."""
        raw = (self.exit_price - self.entry_price) / self.entry_price
        return self.direction * raw * 10_000


@dataclass
class VariantResult:
    variant: V.Variant
    trades: list[Trade] = field(default_factory=list)
    skipped_no_signal: int = 0
    skipped_no_price: int = 0

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def returns(self) -> list[float]:
        return [t.return_bps for t in self.trades]

    @property
    def mean_bps(self) -> float:
        return sum(self.returns) / self.n if self.n else 0.0

    @property
    def std_bps(self) -> float:
        if self.n < 2:
            return 0.0
        m = self.mean_bps
        return math.sqrt(sum((r - m) ** 2 for r in self.returns) / (self.n - 1))

    @property
    def sharpe(self) -> float:
        """Annualised at the pre-registered 24 events a year.

        Event-driven annualisation is a convention, not a law. It is stated so
        the number can be compared across variants, which is all it is for.
        """
        if self.n < 2 or self.std_bps == 0:
            return 0.0
        return (self.mean_bps / self.std_bps) * math.sqrt(EVENTS_PER_YEAR)

    @property
    def hit_rate(self) -> float:
        if not self.n:
            return 0.0
        return sum(1 for r in self.returns if r > 0) / self.n

    @property
    def total_bps(self) -> float:
        return sum(self.returns)


@dataclass
class Experiment:
    results: dict[str, VariantResult]
    events_considered: int
    prereg_hash: str
    instrument: str

    def gap(self, a: str, b: str) -> dict:
        ra, rb = self.results[a], self.results[b]
        return {
            "from": a, "to": b,
            "sharpe_delta": round(rb.sharpe - ra.sharpe, 4),
            "mean_bps_delta": round(rb.mean_bps - ra.mean_bps, 4),
        }

    @property
    def gaps(self) -> list[dict]:
        return [self.gap("A", "B"), self.gap("B", "C"), self.gap("C", "D")]

    @property
    def underpowered(self) -> bool:
        """True when the sample is too small for the gaps to mean anything.

        The differences between arms are small quantities measured against the
        variance of FX returns over 30 minutes. At a handful of events that
        variance swamps them completely, so the ordering is essentially a coin
        toss and reporting it as a finding would be the exact overclaiming this
        project exists to criticise.
        """
        return self.results["A"].n < MIN_EVENTS_FOR_INTERPRETATION

    @property
    def ordering_holds(self) -> bool:
        """H1: reported Sharpe orders D > C > B > A."""
        s = [self.results[k].sharpe for k in ("A", "B", "C", "D")]
        return all(s[i] < s[i + 1] for i in range(3))

    @property
    def largest_source(self) -> str:
        """Which single channel contributes most to the A->D gap in Sharpe."""
        labelled = {
            "revision leakage (A->B)": self.gap("A", "B")["sharpe_delta"],
            "timestamp coarsening (B->C)": self.gap("B", "C")["sharpe_delta"],
            "mid-price assumption (C->D)": self.gap("C", "D")["sharpe_delta"],
        }
        return max(labelled, key=lambda k: labelled[k])


def _price_at(client, instrument: str, when: datetime) -> tuple[float, float] | None:
    """First clean tick at or after `when`, within the pre-registered tolerance."""
    result = client.query(
        "SELECT bid, ask FROM tick_raw "
        " WHERE instrument = %(i)s AND ts >= %(t)s AND ts <= %(u)s AND bid <= ask "
        " ORDER BY ts LIMIT 1",
        parameters={
            "i": instrument,
            "t": when,
            "u": when + timedelta(minutes=V.ENTRY_TOLERANCE_MINUTES),
        },
    )
    if not result.result_rows:
        return None
    bid, ask = result.result_rows[0]
    return float(bid), float(ask)


def run_experiment(
    releases: list[Release], instrument: str = "EURUSD"
) -> Experiment:
    """Run all four variants over the same event set.

    Every instant is priced ONCE and memoised. The variants share most of their
    entry and exit instants (A and B are identical in timing, C and D likewise),
    and the usable-event check needs the same lookups the run does — so a naive
    implementation issues roughly four times the queries it needs. At 72 events
    that was over a thousand ClickHouse round-trips and made the dashboard page
    take minutes.
    """
    client = ch_store.connect()
    results = {v.key: VariantResult(v) for v in V.VARIANTS}
    quotes: dict[datetime, tuple[float, float] | None] = {}

    def price(when: datetime) -> tuple[float, float] | None:
        if when not in quotes:
            quotes[when] = _price_at(client, instrument, when)
        return quotes[when]

    try:
        # An event is only traded if EVERY variant can price it. Otherwise the
        # arms would compare different event sets and the differences would
        # partly measure sample composition rather than contamination.
        usable: list[Release] = []
        for release in releases:
            instants = []
            for variant in V.VARIANTS:
                entry = V.entry_instant(variant, release)
                instants.extend((entry, V.hold_until(entry)))
            if all(price(t) is not None for t in instants):
                usable.append(release)
            else:
                for r in results.values():
                    r.skipped_no_price += 1

        for release in usable:
            for variant in V.VARIANTS:
                res = results[variant.key]
                direction = V.signal(variant, release)
                if direction == 0:
                    res.skipped_no_signal += 1
                    continue
                entry = V.entry_instant(variant, release)
                exit_at = V.hold_until(entry)
                entry_quote = price(entry)
                exit_quote = price(exit_at)
                if entry_quote is None or exit_quote is None:
                    res.skipped_no_price += 1
                    continue
                res.trades.append(
                    Trade(
                        release=release,
                        direction=direction,
                        entry_ts=entry,
                        exit_ts=exit_at,
                        entry_price=V.fill_prices(variant, *entry_quote, direction),
                        exit_price=V.exit_price(variant, *exit_quote, direction),
                    )
                )

        return Experiment(
            results=results,
            events_considered=len(releases),
            prereg_hash=preregistration_hash(),
            instrument=instrument,
        )
    finally:
        client.close()


def release_days_needed(releases: list[Release]) -> list[tuple[str, list[int]]]:
    """(date, hours) the tick ingest must cover for these events.

    Only the hours the experiment reads: 00 UTC for the date-only entry, and
    the block around the release instant for the exact-timestamp entry plus its
    hold. Fetching whole days would be four times the requests for no benefit.
    """
    out = []
    for release in releases:
        hours = {0}
        for variant in V.VARIANTS:
            entry = V.entry_instant(variant, release)
            hours.add(entry.hour)
            hours.add(V.hold_until(entry).hour)
            hours.add((entry + timedelta(minutes=V.ENTRY_TOLERANCE_MINUTES)).hour)
        out.append((release.release_date.isoformat(), sorted(hours)))
    return out


def utc_now() -> datetime:
    return datetime.now(UTC)
