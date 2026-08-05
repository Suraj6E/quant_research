"""Per-currency holiday calendars.

WHAT A CURRENCY HOLIDAY MEANS HERE
----------------------------------
Thin liquidity, not a closed market. FX trades 24/5 through national holidays;
what changes is how many participants are at their desks and therefore how wide
the spread is. The flag this feeds is `holiday_thin` for exactly that reason —
naming it `market_closed` would assert something false.

A pair has two legs and either can be on holiday. EURUSD on US Independence Day
and EURUSD on a German holiday are both affected, differently.

SOURCE AND ITS LIMITS
---------------------
The `holidays` package: free, no account, pure Python, rules rather than a
downloaded dataset. That keeps the project's no-registration constraint intact.

Two honest caveats, both recorded rather than smoothed over:

  * **National holidays are a proxy for market holidays**, not the same thing.
    A bank holiday closes settlement systems; it does not close the FX market.
    The correlation is strong enough to be useful and weak enough that the flag
    is named for thinness rather than closure.
  * **The euro area has no single holiday calendar.** Germany is used as the
    proxy because it is the largest member, but a French or Italian holiday
    thins EUR liquidity too, and German regional holidays are not observed
    euro-wide. This is a known approximation, surfaced in the report.
"""

from __future__ import annotations

from datetime import date

from fxpit.sessions.definitions import CURRENCY_COUNTRY

# Recorded so the approximation is visible wherever the data is used, rather
# than living only in a docstring nobody opens.
CAVEATS: dict[str, str] = {
    "EUR": (
        "German calendar used as the euro-area proxy - no single euro-area "
        "holiday calendar exists, and French or Italian holidays thin EUR "
        "liquidity without appearing here"
    ),
}


def available() -> bool:
    try:
        import holidays  # noqa: F401
    except ImportError:
        return False
    return True


def for_currency(currency: str, start_year: int, end_year: int) -> list[tuple[date, str]]:
    """(date, name) pairs for one currency across a year range.

    Returns an empty list rather than raising when the package is absent or the
    currency is unmapped, because a missing holiday calendar should degrade the
    session layer rather than break it — but `available()` and `unmapped()` let
    a caller report the gap instead of silently treating every day as normal.
    """
    country = CURRENCY_COUNTRY.get(currency)
    if not country:
        return []
    try:
        import holidays as holidays_pkg
    except ImportError:
        return []

    try:
        calendar = holidays_pkg.country_holidays(
            country, years=range(start_year, end_year + 1)
        )
    except Exception:
        return []
    return sorted((day, str(name)) for day, name in calendar.items())


def unmapped(currencies: list[str]) -> list[str]:
    """Currencies with no holiday calendar, so a caller can say so out loud."""
    return [c for c in currencies if c not in CURRENCY_COUNTRY]
