"""Dukascopy .bi5 fetch and decode.

Every gotcha encoded here was confirmed by measurement on 2026-08-04, not read
from documentation. Each one produces plausible-looking wrong data rather than
an error, which is why they are asserted rather than trusted.

WHAT COUNTS AS TRANSFORMATION
-----------------------------
`tick_raw` is immutable and nothing is transformed on ingest. Two operations
here are nonetheless required and are *decoding*, not cleaning:

  * integer -> float via the instrument's decimal factor. Without it there is
    no price at all, only a machine integer.
  * millisecond offset -> absolute UTC timestamp. The file only carries an
    offset from the start of its hour.

Everything else the feed sent is preserved exactly: crossed quotes, zero
spreads, duplicate stamps and out-of-order rows all go in untouched. They are
flagged additively in Phase 2. Dropping a bad tick here would destroy the
evidence that the feed produced it.
"""

from __future__ import annotations

import lzma
import random
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

USER_AGENT = "fxpit-ingest/0.1 (research database; contact via repository)"
BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Per-instrument decimal factor. JPY pairs quote to 3 decimals, the rest to 5.
# Applying the wrong factor yields prices off by two orders of magnitude -
# obvious - or, worse, silently plausible on a pair you are not watching.
DECIMAL_FACTORS: dict[str, int] = {
    "EURUSD": 100_000,
    "GBPUSD": 100_000,
    "AUDUSD": 100_000,
    "NZDUSD": 100_000,
    "USDCHF": 100_000,
    "USDCAD": 100_000,
    "USDJPY": 1_000,
    "EURJPY": 1_000,
    "GBPJPY": 1_000,
}

# Phase 1 starts narrow on purpose (planning.md): 7 majors. Extend backward
# only once a re-run is provably a no-op.
MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]

# One tick is 20 bytes, big-endian. Field ORDER IS ASK BEFORE BID - reversing
# them produces a uniformly negative spread, which looks like a broken feed
# rather than a decode bug.
TICK_STRUCT = struct.Struct(">IIIff")
TICK_SIZE = TICK_STRUCT.size


class FetchStatus(Enum):
    """Why an hour produced the rows it produced.

    EMPTY is not an error and is the single most important distinction here.
    Dukascopy returns HTTP 200 with a zero-byte body for closed sessions - it
    does NOT return 404 (measured; planning.md originally said otherwise). So
    HTTP status alone cannot separate "market closed" from "feed gap", and only
    the Phase 4 session calendar can. Until then EMPTY is recorded faithfully
    and the coverage report surfaces it rather than resolving it.
    """

    OK = "ok"
    EMPTY = "empty"
    MISSING = "missing"
    ERROR = "error"


@dataclass(frozen=True)
class Tick:
    instrument: str
    ts: datetime
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float
    source: str = "dukascopy"


@dataclass(frozen=True)
class HourResult:
    instrument: str
    hour: datetime
    status: FetchStatus
    ticks: list[Tick]
    bytes_downloaded: int
    detail: str = ""


def decimal_factor(instrument: str) -> int:
    try:
        return DECIMAL_FACTORS[instrument]
    except KeyError:
        raise ValueError(
            f"No decimal factor known for {instrument!r}. Add it to "
            f"DECIMAL_FACTORS - guessing would silently corrupt every price."
        ) from None


def hour_url(instrument: str, hour: datetime) -> str:
    """Build the .bi5 URL for one instrument-hour.

    The month index is ZERO-BASED. Confirmed empirically: month 00 returns
    January (mean EURUSD bid 1.09409 on day 09 of 2024) and month 01 returns
    February (1.07665). An off-by-one here returns a different month's prices,
    which are entirely plausible and entirely wrong.
    """
    if hour.tzinfo is None:
        raise ValueError("hour must be timezone-aware UTC")
    h = hour.astimezone(UTC)
    return (
        f"{BASE_URL}/{instrument}/{h.year:04d}/{h.month - 1:02d}/"
        f"{h.day:02d}/{h.hour:02d}h_ticks.bi5"
    )


def decode(payload: bytes, instrument: str, hour: datetime) -> list[Tick]:
    """Decode one .bi5 body into ticks. Order and content are preserved."""
    if not payload:
        return []

    factor = decimal_factor(instrument)
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    raw = decompressor.decompress(payload)

    if len(raw) % TICK_SIZE:
        raise ValueError(
            f"{instrument} {hour:%Y-%m-%d %H}Z: decompressed to {len(raw)} bytes, "
            f"not a multiple of {TICK_SIZE}. Refusing to guess at a partial record."
        )

    base = hour.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    ticks = []
    for offset in range(0, len(raw), TICK_SIZE):
        ms, ask_i, bid_i, ask_vol, bid_vol = TICK_STRUCT.unpack_from(raw, offset)
        ticks.append(
            Tick(
                instrument=instrument,
                ts=base + timedelta(milliseconds=ms),
                bid=bid_i / factor,
                ask=ask_i / factor,
                bid_volume=bid_vol,
                ask_volume=ask_vol,
            )
        )
    return ticks


# Codes that mean "you are going too fast", as opposed to "something broke".
# Measured: 4 concurrent workers at a 0.15s pause earned a burst of 503s from
# the live feed. They need a much longer, jittered backoff than a normal
# transient error, because every worker hits the wall at the same instant and
# a short uniform retry just reproduces the burst.
THROTTLE_CODES = frozenset({429, 503})


def fetch_hour(
    instrument: str,
    hour: datetime,
    *,
    timeout: int = 60,
    retries: int = 5,
    backoff: float = 2.0,
    pause: float = 0.25,
) -> HourResult:
    """Fetch and decode one instrument-hour.

    Retries transient failures; a 404 is a definite answer and is not retried.
    `pause` is a courtesy delay — this is a free service being asked for a
    large number of small files, and the polite thing is also the fast thing
    once throttling is accounted for.
    """
    url = hour_url(instrument, hour)
    last_error = ""

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                retry_after = response.headers.get("Retry-After")
            time.sleep(pause)

            if not payload:
                return HourResult(instrument, hour, FetchStatus.EMPTY, [], 0,
                                  "HTTP 200, zero-byte body")
            ticks = decode(payload, instrument, hour)
            status = FetchStatus.OK if ticks else FetchStatus.EMPTY
            return HourResult(instrument, hour, status, ticks, len(payload))

        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return HourResult(instrument, hour, FetchStatus.MISSING, [], 0, "HTTP 404")
            last_error = f"HTTP {exc.code}"
            if exc.code in THROTTLE_CODES and attempt < retries - 1:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait = float(retry_after) if (retry_after or "").isdigit() else 4.0 * (2**attempt)
                # Jitter so concurrent workers do not resynchronise and
                # recreate the burst that caused the throttle.
                time.sleep(wait + random.uniform(0, 1.5))
                continue
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries - 1:
            time.sleep(backoff**attempt + random.uniform(0, 0.4))

    return HourResult(instrument, hour, FetchStatus.ERROR, [], 0, last_error)


def hours_between(
    start: datetime, end: datetime, only_hours: set[int] | None = None
) -> list[datetime]:
    """Every UTC hour in [start, end), optionally restricted to `only_hours`.

    The hour filter exists for targeted ingest: the Phase 6 experiment reads a
    handful of hours around each macro release, and fetching whole days would
    be four times the requests for no benefit. The ledger still records exactly
    which hours were attempted, so partial coverage stays visible rather than
    looking like a gap.

    Weekend hours are included on purpose -
    they are fetched, come back empty, and are recorded as such. Skipping them
    here would leave the ledger unable to distinguish "closed" from "never
    attempted".
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    stop = end.astimezone(UTC)
    out = []
    while cursor < stop:
        if only_hours is None or cursor.hour in only_hours:
            out.append(cursor)
        cursor += timedelta(hours=1)
    return out
