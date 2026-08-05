"""Session definitions and UTC window generation.

THE CENTRAL IDEA
----------------
Every window is defined in **local wall-clock time with an IANA zone**, then
converted to UTC. Nothing here contains a UTC constant.

That is not a stylistic preference. Daylight saving is the single most reliable
source of silent timezone bugs in FX work, and the reason is that UTC constants
look correct all year and are wrong for a few weeks of it. The London session
is 08:00–17:00 *in London*; expressing that as "07:00–16:00 UTC" is right in
summer and an hour out in winter.

Converting from local time makes the whole problem disappear into `zoneinfo`,
including the awkward cases:

  * US and EU shift on DIFFERENT DATES, so for two to three weeks each spring
    and autumn the London–New York overlap is an hour longer or shorter than
    normal. Nothing here special-cases that; it just comes out.
  * Sydney is in the southern hemisphere and shifts the OPPOSITE way, in
    October and April rather than March and November.
  * Tokyo has no daylight saving at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Sessions in local wall-clock time. Hours are the conventional liquidity
# windows, not exchange hours — FX has no exchange.
SESSIONS: dict[str, tuple[str, time, time]] = {
    "sydney": ("Australia/Sydney", time(7, 0), time(16, 0)),
    "tokyo": ("Asia/Tokyo", time(9, 0), time(18, 0)),
    "london": ("Europe/London", time(8, 0), time(17, 0)),
    "new_york": ("America/New_York", time(8, 0), time(17, 0)),
}

# The FX week and rollover both hang off 17:00 New York.
MARKET_ZONE = "America/New_York"
WEEK_OPEN = time(17, 0)   # Sunday
WEEK_CLOSE = time(17, 0)  # Friday
ROLLOVER_START = time(17, 0)
ROLLOVER_END = time(18, 0)

# Which currencies make up each pair. Both legs matter: a US holiday and a
# German holiday affect EURUSD differently but both affect it.
PAIR_LEGS: dict[str, tuple[str, str]] = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "USDCHF": ("USD", "CHF"),
    "AUDUSD": ("AUD", "USD"),
    "USDCAD": ("USD", "CAD"),
    "NZDUSD": ("NZD", "USD"),
    "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
}

# Currency -> the country calendar used for its holidays.
CURRENCY_COUNTRY: dict[str, str] = {
    "USD": "US",
    "EUR": "DE",  # Germany as the euro-area proxy; see the caveat in holidays.py
    "GBP": "GB",
    "JPY": "JP",
    "CHF": "CH",
    "AUD": "AU",
    "CAD": "CA",
    "NZD": "NZ",
}


@dataclass(frozen=True)
class Window:
    label: str
    start: datetime
    end: datetime
    zone: str

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def _local_to_utc(day: date, wall: time, zone: str) -> datetime:
    """Attach a wall-clock time to a date in `zone`, then convert to UTC.

    `fold=0` resolves the autumn ambiguity to the FIRST occurrence of a
    repeated hour, which is the earlier instant. On a spring-forward gap
    zoneinfo maps the nonexistent time forward. Both are deterministic; the
    important part is that they are decided here, once, rather than differently
    in each caller.
    """
    naive = datetime.combine(day, wall).replace(fold=0)
    return naive.replace(tzinfo=ZoneInfo(zone)).astimezone(UTC)


def session_windows(start: date, end: date) -> list[Window]:
    """One window per session per calendar day in [start, end).

    Weekend days are generated too and then intersected with the market window
    by the caller — a session that falls entirely outside trading hours is
    still a real fact about the clock, and dropping it here would conflate
    "no session" with "market closed".
    """
    out: list[Window] = []
    for name, (zone, open_t, close_t) in SESSIONS.items():
        day = start
        while day < end:
            begin = _local_to_utc(day, open_t, zone)
            finish = _local_to_utc(day, close_t, zone)
            if finish > begin:
                out.append(Window(name, begin, finish, zone))
            day += timedelta(days=1)
    return out


def market_windows(start: date, end: date) -> list[Window]:
    """FX trading weeks: Sunday 17:00 NY to Friday 17:00 NY.

    Generated week by week from local time, so the UTC boundary moves by an
    hour across DST transitions without anything here knowing that DST exists.
    """
    out: list[Window] = []
    day = start - timedelta(days=7)
    while day < end:
        if day.weekday() == 6:  # Sunday
            opens = _local_to_utc(day, WEEK_OPEN, MARKET_ZONE)
            closes = _local_to_utc(day + timedelta(days=5), WEEK_CLOSE, MARKET_ZONE)
            out.append(Window("market", opens, closes, MARKET_ZONE))
        day += timedelta(days=1)
    return out


def rollover_windows(start: date, end: date) -> list[Window]:
    """17:00-18:00 New York on each trading day.

    This is the correction for the Phase 2 detector, which hardcoded 21:00 UTC
    and was therefore an hour wrong for the weeks when US daylight saving is
    out of step with the rest of the year.
    """
    out: list[Window] = []
    day = start
    while day < end:
        if day.weekday() < 5:  # Mon-Fri in NY terms
            begin = _local_to_utc(day, ROLLOVER_START, MARKET_ZONE)
            finish = _local_to_utc(day, ROLLOVER_END, MARKET_ZONE)
            out.append(Window("rollover", begin, finish, MARKET_ZONE))
        day += timedelta(days=1)
    return out


def london_ny_overlap(day: date) -> tuple[datetime, datetime, float]:
    """The London-New York overlap for one date, and its length in hours.

    The most liquid window of the FX day, and the one that changes length
    during the DST offset weeks. Returned as a measurement rather than a
    constant because it genuinely is not constant.
    """
    l_zone, l_open, l_close = SESSIONS["london"]
    n_zone, n_open, n_close = SESSIONS["new_york"]
    start = max(_local_to_utc(day, l_open, l_zone), _local_to_utc(day, n_open, n_zone))
    end = min(_local_to_utc(day, l_close, l_zone), _local_to_utc(day, n_close, n_zone))
    hours = max((end - start).total_seconds() / 3600, 0.0)
    return start, end, hours


def dst_offset_weeks(year: int) -> list[dict]:
    """Dates where the London-New York overlap differs from its modal length.

    These are the two-to-three week windows each spring and autumn when the US
    and EU have shifted and the other has not. Computed rather than tabulated,
    so it stays correct if a jurisdiction changes its rules.
    """
    day = date(year, 1, 1)
    lengths: list[tuple[date, float]] = []
    while day < date(year + 1, 1, 1):
        if day.weekday() < 5:
            lengths.append((day, round(london_ny_overlap(day)[2], 2)))
        day += timedelta(days=1)

    counts: dict[float, int] = {}
    for _, hours in lengths:
        counts[hours] = counts.get(hours, 0) + 1
    modal = max(counts, key=lambda k: counts[k])

    out: list[dict] = []
    run: list[tuple[date, float]] = []
    for entry in lengths:
        if entry[1] != modal:
            run.append(entry)
        elif run:
            out.append(_summarise_run(run, modal))
            run = []
    if run:
        out.append(_summarise_run(run, modal))
    return out


def _summarise_run(run: list[tuple[date, float]], modal: float) -> dict:
    hours = run[0][1]
    return {
        "start": run[0][0],
        "end": run[-1][0],
        "trading_days": len(run),
        "overlap_hours": hours,
        "normal_hours": modal,
        "delta_hours": round(hours - modal, 2),
    }
