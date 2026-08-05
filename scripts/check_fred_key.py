"""Verify a FRED/ALFRED API key actually returns point-in-time vintages.

    python scripts/check_fred_key.py

Reads FRED_API_KEY from .env. Proves the key works by asking for the SAME
reference period at two different real-time dates and confirming the answers
differ — which is exactly what the keyless CSV endpoint failed to do.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("No .env found. Copy .env.example to .env first.")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def api(path, **params):
    params.setdefault("file_type", "json")
    params["api_key"] = KEY
    url = f"https://api.stlouisfed.org/fred/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "fxpit-key-check/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


load_env()
KEY = os.environ.get("FRED_API_KEY", "").strip()

if not KEY:
    sys.exit("FRED_API_KEY is empty in .env.\n"
             "Get one at https://fredaccount.stlouisfed.org/apikeys, paste it in, re-run.")
if len(KEY) != 32 or not KEY.isalnum() or not KEY.islower():
    print(f"WARNING: key looks unusual (len={len(KEY)}). "
          "FRED keys are 32 lowercase alphanumerics.\n")

print("=" * 66)
print("1. Key accepted?")
print("=" * 66)
try:
    meta = api("series", series_id="PAYEMS")["seriess"][0]
    print(f"  OK  {meta['id']} — {meta['title']}")
    print(f"      units={meta['units_short']}  freq={meta['frequency_short']}")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "replace")[:300]
    sys.exit(f"  FAILED  HTTP {e.code}\n  {body}\n\n"
             "  400 usually means the key is malformed or not yet active.")

print("\n" + "=" * 66)
print("2. How many vintages exist?")
print("=" * 66)
vd = api("series/vintagedates", series_id="PAYEMS", limit=10000)["vintage_dates"]
print(f"  {len(vd)} vintage dates, {vd[0]} .. {vd[-1]}")

print("\n" + "=" * 66)
print("3. THE REAL TEST — same ref period, two real-time dates")
print("=" * 66)
REF = "2024-01-01"  # January 2024 payrolls


def value_as_of(rt):
    obs = api("series/observations", series_id="PAYEMS",
              observation_start=REF, observation_end=REF,
              realtime_start=rt, realtime_end=rt)["observations"]
    return [(o["value"], o["realtime_start"]) for o in obs]


early, late = value_as_of("2024-02-05"), value_as_of("2024-09-05")
print(f"  ref_period {REF}")
print(f"    as of 2024-02-05 (first print): {early}")
print(f"    as of 2024-09-05 (revised):     {late}")

if early and late and early[0][0] != late[0][0]:
    print("\n  -> Vintages CONFIRMED. realtime_start/realtime_end map directly")
    print("     onto known_at. ALFRED is usable as an enrichment source.")
elif early and late:
    print("\n  -> Values identical. Either this period was never revised,")
    print("     or vintage access is not active on this key. Try series_id=GDPC1.")
else:
    print("\n  -> No observations returned; check the reference period.")

print("\n" + "=" * 66)
print("4. Full revision history for one period")
print("=" * 66)
allobs = api("series/observations", series_id="PAYEMS",
             observation_start=REF, observation_end=REF,
             realtime_start="1776-07-04", realtime_end="9999-12-31")["observations"]
print(f"  {len(allobs)} distinct vintage(s) of {REF}:")
for o in allobs:
    print(f"    known from {o['realtime_start']} to {o['realtime_end']}:  {o['value']}")
print("\n  Each row is one (ref_period, known_at, value) triple —")
print("  it maps 1:1 onto the macro_observation table.")
