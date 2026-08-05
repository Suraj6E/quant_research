"""LIVE and RECORDED data sources.

Nothing in this module invents a number. Functions either shell out to the
real thing (pytest, docker) or read files that are in the repository. Where a
value was measured once and frozen, it lives in `RECORDED_*` with the instant
it was captured, because a stale real number is a different thing from a
fresh one and the UI says which it is showing.
"""

from __future__ import annotations

import csv
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures"

# --------------------------------------------------------------------------
# RECORDED — measured on the dates stated, frozen here.
# --------------------------------------------------------------------------

CAPTURED_AT = "2026-08-04"

RECORDED_SOURCE_CHECKS = [
    {
        "source": "Dukascopy",
        "verdict": "pass",
        "detail": "4,508 EURUSD ticks for 2024-01-09 10:00 UTC. Zero crossed quotes, "
                  "monotonic timestamps, spreads 0.1-0.5 pip.",
        "gotcha": "Month indices are zero-based - confirmed empirically (month 00 -> mean "
                  "bid 1.09409 = January; month 01 -> 1.07665 = February). JPY pairs use a "
                  "1e3 decimal factor, EUR pairs 1e5.",
    },
    {
        "source": "Philadelphia Fed RTDSM",
        "verdict": "pass",
        "detail": "8 spreadsheets across the payrolls / CPI / GDP pages, no account. "
                  "employMvMd.xlsx (2.2 MB) is the bitemporal grid: columns are vintages, "
                  "rows are reference periods.",
        "gotcha": "URLs need a Sitecore '?sc_lang=en&hash=...' query string. Without it you "
                  "get a soft-404 - HTTP 200 serving an HTML error page. The hash rotates, so "
                  "scrape the series page rather than hardcoding.",
    },
    {
        "source": "ALFRED (keyless CSV)",
        "verdict": "fail",
        "detail": "Accepts vintage_date and silently ignores it. Four different vintage "
                  "dates (2005, 2015, 2024, none) returned byte-identical payloads, "
                  "sha256 177efe81de346565, all serving current revised data.",
        "gotcha": "This is the project's own failure mode in the wild: no error, no warning, "
                  "just future data wearing a past date.",
    },
    {
        "source": "ALFRED (API key)",
        "verdict": "pass",
        "detail": "857 vintage dates for PAYEMS, 1955-05-06 to 2026-07-02. "
                  "realtime_start/realtime_end map 1:1 onto known_at.",
        "gotcha": "The real-time axis is date-granularity: it gives the vintage date, not the "
                  "08:30 ET release time. Broadens coverage; does not solve timing.",
    },
]

# The real PAYEMS revision chain for reference period 2024-01, pulled from
# ALFRED and verified by scripts/check_fred_key.py.
RECORDED_PAYEMS_VINTAGES = [
    ("2024-02-02", 157700.0),
    ("2024-03-08", 157533.0),
    ("2024-04-05", 157560.0),
    ("2025-02-07", 157049.0),
    ("2026-02-11", 157032.0),
]

INVARIANTS = [
    ("as_of(t) is the only sanctioned read path",
     "Direct table SELECTs outside the query layer are an audit failure."),
    ("Bitemporality is two columns, always both",
     "ref_period describes the period; known_at is when it became public."),
    ("known_at is TIMESTAMPTZ, never DATE",
     "An 08:30 ET release and an 08:31 ET price are different objects."),
    ("tick_raw is immutable",
     "All cleaning is additive - detectors write to a separate tick_flag table."),
    ("Bid and ask are always kept separate",
     "Never mid-only. An assumed spread is how backtests lie."),
    ("Disagreements are logged, never suppressed",
     "The Dukascopy/HistData disagreement rate is itself a deliverable."),
    ("Unknown means unknown",
     "An unsourced release time is recorded as unknown, never assumed 08:30 ET."),
]

LIMITATIONS = [
    ("No consolidated tape",
     "There is no canonical FX price. Dukascopy is one ECN's aggregated view; every "
     "result is conditional on this feed."),
    ("Volume is not volume",
     "Dukascopy's volume field reflects their own pool. Nothing should be built on it."),
    ("Small cross-section",
     "~8 majors against 5,000+ US equities. Less statistical power, so overfitting risk "
     "is higher, not lower."),
    ("No free historical consensus forecasts",
     "Surprise measures must come from revisions or synthetic forecasts."),
    ("No forward points",
     "Carry must proxy with policy-rate differentials, and CIP has failed persistently "
     "since 2008 - so the proxy error correlates with funding stress."),
    ("Equity coverage is CFD-derived",
     "Not exchange tape, no real volume, no corporate actions."),
    ("Retail execution assumptions",
     "No slippage, rejection, requoting or last-look. Fills are optimistic by an "
     "unmeasured amount."),
]


# --------------------------------------------------------------------------
# LIVE — measured at request time.
# --------------------------------------------------------------------------


@dataclass
class TestResults:
    passed: int
    failed: int
    total: int
    duration: str
    failing_names: list[str]
    all_reds_expected: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        """Green here means 'red for the right reason'. Phase 0 is supposed to
        fail; what must not happen is a failure that is not NotImplementedError.
        """
        return self.all_reds_expected and not self.error


@lru_cache(maxsize=1)
def _test_cache_key() -> float:
    return time.time() // 30  # re-run at most twice a minute


def run_tests(force: bool = False) -> TestResults:
    """Run the acceptance suite and parse the result."""
    if force:
        _run_tests_cached.cache_clear()
    return _run_tests_cached(_test_cache_key() if not force else time.time())


@lru_cache(maxsize=4)
def _run_tests_cached(_key: float) -> TestResults:
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", "--no-header", "-q", "--tb=line"],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return TestResults(0, 0, 0, "-", [], False, error=f"{type(exc).__name__}: {exc}")

    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+) failed,\s*(\d+) passed in ([\d.]+)s", out)
    if not m:
        m2 = re.search(r"(\d+) passed in ([\d.]+)s", out)
        if m2:
            return TestResults(int(m2.group(1)), 0, int(m2.group(1)), f"{m2.group(2)}s", [], True)
        return TestResults(0, 0, 0, "-", [], False, error="could not parse pytest output")

    failed, passed = int(m.group(1)), int(m.group(2))
    names = re.findall(r"FAILED (\S+)", out)
    # The whole point: every red must be NotImplementedError. Anything else is
    # a real break masquerading as an expected failure.
    unexpected = re.findall(r"(AssertionError|TypeError|KeyError|ImportError|NameError)", out)
    return TestResults(
        passed=passed, failed=failed, total=passed + failed,
        duration=f"{m.group(3)}s", failing_names=names,
        all_reds_expected=not unexpected,
    )


def docker_status() -> list[dict[str, str]]:
    """Container health, read from docker compose."""
    try:
        proc = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Service}}\t{{.State}}\t{{.Health}}"],
            cwd=ROOT, capture_output=True, text=True, timeout=25,
        )
        if proc.returncode != 0:
            return [{"service": "docker", "state": "unavailable",
                     "health": "daemon not running"}]
        rows = []
        for line in proc.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                rows.append({"service": parts[0], "state": parts[1], "health": parts[2]})
        return rows or [{"service": "docker", "state": "no containers", "health": "-"}]
    except Exception as exc:
        return [{"service": "docker", "state": "error", "health": type(exc).__name__}]


def _read_fixture(name: str) -> list[dict[str, str]]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def macro_fixture() -> list[dict]:
    rows = []
    for r in _read_fixture("macro_vintages.csv"):
        rows.append({
            "series_id": r["series_id"],
            "ref_period": date.fromisoformat(r["ref_period"]),
            "known_at": datetime.fromisoformat(r["known_at"]),
            "value": float(r["value"]) if r["value"] else None,
            "vintage_seq": int(r["vintage_seq"]),
        })
    return rows


def tick_fixture() -> list[dict]:
    rows = []
    for r in _read_fixture("ticks.csv"):
        bid, ask = float(r["bid"]), float(r["ask"])
        rows.append({
            "instrument": r["instrument"],
            "ts": r["ts"],
            "bid": bid,
            "ask": ask,
            "spread": ask - bid,
            "source": r["source"],
        })
    return rows


def bars_fixture() -> list[dict]:
    return [
        {
            "instrument": r["instrument"], "ts": r["ts"], "source": r["source"],
            "bid_close": float(r["bid_close"]), "ask_close": float(r["ask_close"]),
        }
        for r in _read_fixture("bars_cross_feed.csv")
    ]


# --------------------------------------------------------------------------
# Phase 1 — real ingest state, read from the ledger and tick_raw.
#
# These replaced demo generators when Phase 1 landed. Each degrades to an empty
# result rather than raising, so the dashboard still renders with the stack
# stopped; the caller decides whether "no data" means "not ingested" or
# "database unreachable".
# --------------------------------------------------------------------------


def ingest_summary() -> list[dict]:
    """Per-instrument ledger totals. Empty list if the stack is unreachable."""
    try:
        from fxpit.ingest import ledger

        conn = ledger.connect()
        try:
            ledger.ensure_schema(conn)
            return ledger.summary(conn)
        finally:
            conn.close()
    except Exception:
        return []


def ingest_monthly_coverage() -> list[dict]:
    try:
        from fxpit.ingest import ledger

        conn = ledger.connect()
        try:
            ledger.ensure_schema(conn)
            return ledger.monthly_coverage(conn)
        finally:
            conn.close()
    except Exception:
        return []


def tick_store_stats() -> list[dict]:
    """What is actually in tick_raw, read from ClickHouse."""
    try:
        from fxpit.ingest import store

        client = store.connect()
        try:
            return store.rows_by_instrument(client)
        finally:
            client.close()
    except Exception:
        return []


def ingest_reconciliation() -> dict:
    """Do the ledger and tick_raw agree?

    A mismatch means rows were written without being recorded, or recorded
    without being written — either way the coverage report is lying, which is
    the one thing the ledger exists to prevent.
    """
    ledger_rows = sum(r["ticks"] for r in ingest_summary())
    store_rows = sum(r["ticks"] for r in tick_store_stats())
    return {
        "ledger_ticks": ledger_rows,
        "store_ticks": store_rows,
        "agree": ledger_rows == store_rows,
    }


def revised_period_count() -> int:
    """How many (series, ref_period) pairs actually got revised in the fixture."""
    by_key: dict[tuple[str, date], list] = {}
    for r in macro_fixture():
        by_key.setdefault((r["series_id"], r["ref_period"]), []).append(r)
    n = 0
    for rows in by_key.values():
        rows.sort(key=lambda r: r["known_at"])
        if rows[0]["value"] != rows[-1]["value"]:
            n += 1
    return n
