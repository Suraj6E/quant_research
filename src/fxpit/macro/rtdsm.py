"""Philadelphia Fed Real-Time Data Set for Macroeconomists — download and parse.

RTDSM publishes each series as a spreadsheet grid: rows are reference periods,
columns are vintages. One cell is therefore exactly one bitemporal fact —
"this is what the series said about period R, as of vintage V" — which maps
1:1 onto `macro_observation`.

DOWNLOAD GOTCHA (measured 2026-08-04)
------------------------------------
File URLs carry a Sitecore query string, `?sc_lang=en&hash=...`. Without it the
server returns **HTTP 200 serving an HTML error page** rather than a 404, so a
naive downloader saves 18 KB of HTML under a .xlsx name and fails later at
parse time with a confusing error. The hash also rotates, so it cannot be
hardcoded — the series page is scraped for links each run.

TIMESTAMP GOTCHA
----------------
A vintage column identifies a MONTH, not a release time. RTDSM cannot tell you
whether a value was public on the 3rd or the 28th. `known_at` is therefore
placed at the last instant of the vintage month with precision='month', which
biases every query toward withholding rather than leaking. Assuming 08:30 ET on
the release date would be inventing precision.
"""

from __future__ import annotations

import html as htmlmod
import io
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

BASE = "https://www.philadelphiafed.org"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# Key fix: the extension is followed by a query string, so the pattern must
# allow one. An earlier version without `(?:\?[^"']*)?` found zero links on a
# page that was full of them.
FILE_LINK = re.compile(r'href=["\']([^"\']*\.(?:xlsx?|csv)(?:\?[^"\']*)?)["\']', re.I)

# Vintage column headers look like EMPLOY64M12 (monthly) or ROUTPUT65Q1
# (quarterly). The two-digit year pivots at 64 because RTDSM begins in 1964.
VINTAGE_RE = re.compile(r"^([A-Z]+)(\d{2})(?:M(\d{1,2})|Q(\d))$", re.I)

# Reference periods look like 1943:11 or 1947:Q1.
REF_MONTH_RE = re.compile(r"^(\d{4}):(\d{1,2})$")
REF_QUARTER_RE = re.compile(r"^(\d{4}):Q(\d)$", re.I)


@dataclass(frozen=True)
class Series:
    """One RTDSM series and the page it is published on."""

    series_id: str
    page: str
    file_hint: str
    description: str


CATALOGUE = [
    Series("EMPLOY", "/surveys-and-data/real-time-data-research/employ",
           "employMvMd", "Nonfarm payroll employment, monthly vintages"),
    Series("CPI", "/surveys-and-data/real-time-data-research/cpi",
           "cpiQvMd", "Consumer price index, monthly vintages"),
    Series("ROUTPUT", "/surveys-and-data/real-time-data-research/routput",
           "ROUTPUTQvQd", "Real GNP/GDP, quarterly vintages"),
]


@dataclass(frozen=True)
class Observation:
    series_id: str
    ref_period: date
    vintage_year: int
    vintage_month: int
    value: float | None


def _fetch(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def discover_file_url(series: Series) -> str | None:
    """Scrape the series page for its vintage spreadsheet link.

    Scraped rather than hardcoded because the Sitecore hash rotates.
    """
    html = _fetch(BASE + series.page).decode("utf-8", "replace")
    links = [htmlmod.unescape(m) for m in FILE_LINK.findall(html)]
    for link in links:
        name = link.split("/")[-1].split("?")[0].lower()
        if name.startswith(series.file_hint.lower()):
            return urllib.parse.urljoin(BASE + series.page, link)
    return None


def download(series: Series) -> bytes:
    url = discover_file_url(series)
    if not url:
        raise RuntimeError(
            f"No vintage file found on {BASE + series.page} matching "
            f"{series.file_hint!r}. The page layout may have changed."
        )
    payload = _fetch(url)
    # A Sitecore soft-404 is HTTP 200 with HTML. Detect it here rather than
    # letting the parser fail later with a confusing message.
    if payload[:2] != b"PK":
        raise RuntimeError(
            f"{url} returned {len(payload)} bytes that are not a valid xlsx "
            f"(likely a soft-404 HTML error page — check the hash query string)."
        )
    return payload


def _parse_ref_period(raw: str) -> date | None:
    raw = str(raw).strip()
    if m := REF_MONTH_RE.match(raw):
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1)
    if m := REF_QUARTER_RE.match(raw):
        year, quarter = int(m.group(1)), int(m.group(2))
        if 1 <= quarter <= 4:
            return date(year, 3 * (quarter - 1) + 1, 1)
    return None


def _parse_vintage(header: str) -> tuple[int, int] | None:
    """Column header -> (vintage_year, vintage_month).

    A quarterly vintage is placed at the LAST month of its quarter, which is
    the conservative choice: it never claims the data was available earlier
    than it was.
    """
    m = VINTAGE_RE.match(str(header).strip())
    if not m:
        return None
    yy = int(m.group(2))
    year = 1900 + yy if yy >= 64 else 2000 + yy
    if m.group(3):
        month = int(m.group(3))
        if not 1 <= month <= 12:
            return None
    else:
        month = 3 * int(m.group(4))
    return year, month


def parse(payload: bytes, series_id: str) -> list[Observation]:
    """Parse the vintage grid into one Observation per non-empty cell.

    Reads the sheet XML directly rather than via openpyxl: the grid is a plain
    rectangle of numbers and adding a dependency to read it would not earn its
    place.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared = _shared_strings(archive)
        sheet_name = next(
            (n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")), None
        )
        if not sheet_name:
            raise RuntimeError("workbook contains no worksheet")
        grid = _sheet_grid(archive.read(sheet_name).decode("utf-8", "replace"), shared)

    if not grid:
        return []

    header = grid[0]
    vintages: dict[int, tuple[int, int]] = {}
    for col, cell in enumerate(header):
        if col == 0:
            continue
        parsed = _parse_vintage(cell)
        if parsed:
            vintages[col] = parsed

    out: list[Observation] = []
    for row in grid[1:]:
        if not row:
            continue
        ref = _parse_ref_period(row[0])
        if ref is None:
            continue
        for col, (vy, vm) in vintages.items():
            if col >= len(row):
                continue
            raw = str(row[col]).strip()
            if not raw or raw.upper() in {"#N/A", "NA", "."}:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            out.append(Observation(series_id, ref, vy, vm, value))
    return out


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml").decode("utf-8", "replace")
    except KeyError:
        return []
    return [
        re.sub(r"<[^>]+>", "", chunk)
        for chunk in re.findall(r"<si>(.*?)</si>", xml, re.S)
    ]


def _sheet_grid(xml: str, shared: list[str]) -> list[list[str]]:
    rows = []
    for row_xml in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
        cells: dict[int, str] = {}
        for cell_xml in re.findall(r"<c\b(.*?)(?:/>|>(.*?)</c>)", row_xml, re.S):
            attrs, body = cell_xml
            ref = re.search(r'r="([A-Z]+)\d+"', attrs)
            if not ref:
                continue
            col = _col_index(ref.group(1))
            value = ""
            if body:
                if 't="s"' in attrs:
                    idx = re.search(r"<v>(\d+)</v>", body)
                    if idx and int(idx.group(1)) < len(shared):
                        value = shared[int(idx.group(1))]
                elif 't="inlineStr"' in attrs:
                    txt = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                    value = txt.group(1) if txt else ""
                else:
                    num = re.search(r"<v>(.*?)</v>", body, re.S)
                    value = num.group(1) if num else ""
            cells[col] = htmlmod.unescape(value)
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def vintage_known_at(year: int, month: int) -> datetime:
    """Last instant of the vintage month, in UTC.

    Conservative on purpose. RTDSM says only that a value was current during
    this month; placing it at month end means a point-in-time query treats it
    as not-yet-public for as long as the data permits. Under-reporting what was
    knowable is a conservative research error. Over-reporting it is look-ahead
    bias, which is the failure this database exists to prevent.
    """
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_moment = datetime.combine(first_next, datetime.min.time(), tzinfo=UTC)
    return last_moment - timedelta(microseconds=1)
