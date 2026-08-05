"""Data provenance — the load-bearing idea of this UI.

This project exists to stop unreal information passing as real. A dashboard
that renders demo numbers in the same visual register as measured ones commits
exactly that error, one layer up. So provenance is not a footnote here: every
panel carries a `Provenance`, the badge is rendered by the layout rather than
by each template, and there is deliberately no way to draw a panel without
declaring one.

Three levels, in descending order of trust:

  LIVE     measured from this repository at request time (test runs, container
           health, fixture contents). Re-reading gives a fresh answer.
  RECORDED real measurements captured from a verified external source at a
           stated instant. Real, but a snapshot — it can go stale.
  DEMO     synthetic. Shaped to look plausible so layout can be judged, but it
           describes nothing. Every DEMO panel names the phase that will
           replace it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Provenance(Enum):
    LIVE = "live"
    RECORDED = "recorded"
    DEMO = "demo"

    @property
    def label(self) -> str:
        return {"live": "Live", "recorded": "Recorded", "demo": "Demo data"}[self.value]

    @property
    def blurb(self) -> str:
        return {
            "live": "Measured from this repository at page load.",
            "recorded": "Real measurement captured from a verified source at a stated time.",
            "demo": "Synthetic. Describes nothing — shape only.",
        }[self.value]


@dataclass(frozen=True)
class Panel:
    """One card. `provenance` is required and has no default on purpose."""

    title: str
    provenance: Provenance
    body: str = ""
    note: str = ""
    unblocked_by: str = ""
    table: TableView | None = None

    def __post_init__(self) -> None:
        if self.provenance is Provenance.DEMO and not self.unblocked_by:
            raise ValueError(
                f"DEMO panel {self.title!r} must name the phase that replaces it. "
                "Unattributed demo data is how a mock-up gets mistaken for a result."
            )


@dataclass(frozen=True)
class TableView:
    """The WCAG-clean twin of a chart.

    Required rather than optional: the validated palette puts light-mode aqua
    at 2.74:1 against the surface, which triggers the relief rule — a chart
    using it must ship visible labels or a table view. Making the table
    mandatory means no chart can regress out of compliance.
    """

    columns: list[str]
    rows: list[list[str]]
    caption: str = ""


@dataclass(frozen=True)
class PhaseStatus:
    number: int
    name: str
    weeks: str
    state: str  # done | next | planned
    exit_criterion: str
    summary: str
    panels: list[Panel] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return f"phase-{self.number}"
