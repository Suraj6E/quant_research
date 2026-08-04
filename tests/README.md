# Tests

Empty until Phase 0. `pytest` currently collects nothing and exits with code 5.

Phase 0 writes four test families against an empty schema, and **they are
supposed to fail** — a red suite is the deliverable. See `planning.md` §6.

| Family | Assertion |
|---|---|
| No-clairvoyance | For N random macro facts, `as_of(known_at − 1s)` returns nothing for that fact |
| Revision | For ≥50 series-periods where first print ≠ final, `as_of` between them returns the first print |
| Tick sanity | Timestamps non-decreasing within instrument; no `bid > ask`; no negative spread; no duplicate `(instrument, ts, source)` |
| Cross-feed | Dukascopy and HistData M1 bars agree within tolerance; every disagreement logged, not suppressed |

Mark tests needing a live stack with `@pytest.mark.integration`, and the
point-in-time families with `@pytest.mark.acceptance`.
