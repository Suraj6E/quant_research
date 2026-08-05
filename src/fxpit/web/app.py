"""FastAPI application.

Framework note (the full argument is in docs/ui-design.md): Django was
considered and rejected. Its centre of gravity is an ORM plus an admin that
generate direct table access, and this project's first success criterion is
that *no* read bypasses `as_of()`. A framework whose main affordance is
"query the table directly" would push against the invariant on every screen.
FastAPI carries no ORM, so the UI can only consume the query layer.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fxpit.web import demo, live
from fxpit.web.charts import bar_chart, heatmap, legend, line_chart, step_timeline
from fxpit.web.provenance import Provenance

HERE = Path(__file__).parent
app = FastAPI(title="fxpit - Point-in-Time FX Research Database", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["Provenance"] = Provenance
templates.env.globals["legend"] = legend

PHASES = [
    (
        0,
        "Acceptance tests",
        "1 week",
        "done",
        "Each test's failure mode explainable in one sentence",
    ),
    (1, "Tick ingest", "2-3 weeks", "done", "A re-run produces zero new rows and zero errors"),
    (
        2,
        "Cleaning layer",
        "2 weeks",
        "done",
        "For any instrument-day, list every flagged tick and why",
    ),
    (3, "Bitemporal macro", "2 weeks", "done", "No-clairvoyance and revision tests green"),
    (
        4,
        "Session & calendar",
        "1-2 weeks",
        "done",
        "For any timestamp: which session, rollover?, holiday?",
    ),
    (
        5,
        "Validation harness",
        "2 weeks",
        "next",
        "Green scheduled suite plus a reconciliation report",
    ),
    (
        6,
        "Contamination experiment",
        "1-2 weeks",
        "planned",
        "Each contamination source sized in bps or Sharpe units",
    ),
]


def ctx(**kw) -> dict:
    """Template context. Starlette takes the request separately now."""
    return {"phases": PHASES, "captured_at": live.CAPTURED_AT, **kw}


@app.get("/", response_class=HTMLResponse)
def research(request: Request):
    """The prospectus is the front door; the dashboard is the instrument.

    A reader arriving cold needs the problem and the argument before any panel
    of numbers means anything.
    """
    timeline = step_timeline(
        live.RECORDED_PAYEMS_VINTAGES,
        places=0,
        unit="k",
        caption="US nonfarm payrolls, reference period January 2024, by vintage",
    )
    return templates.TemplateResponse(
        request, "research.html", ctx(active="research", timeline=timeline)
    )


@app.get("/status", response_class=HTMLResponse)
def overview(request: Request):
    results = live.run_tests()
    return templates.TemplateResponse(
        request,
        "overview.html",
        ctx(
            active="status",
            results=results,
            containers=live.docker_status(),
            checks=live.RECORDED_SOURCE_CHECKS,
            invariants=live.INVARIANTS,
            limitations=live.LIMITATIONS,
        ),
    )


@app.get("/phase/0", response_class=HTMLResponse)
def phase0(request: Request):
    results = live.run_tests()
    families = [
        (
            "No-clairvoyance",
            "test_no_clairvoyance.py",
            5,
            "as_of(t) returned information that did not exist at t, so every backtest "
            "built on it can see the future.",
        ),
        (
            "Revision",
            "test_revision.py",
            6,
            "The query returned a revised value that was not published until later, so "
            "the backtest traded on a number nobody had.",
        ),
        (
            "Tick sanity",
            "test_tick_sanity.py",
            10,
            "A corrupt quote reached research code without announcing itself, so a "
            "spread or return computed from it is silently wrong.",
        ),
        (
            "Cross-feed",
            "test_cross_feed.py",
            7,
            "The feeds disagreed and the system picked a winner without telling anyone, "
            "so a data-quality problem was laundered into a confident-looking number.",
        ),
    ]
    ticks = live.tick_fixture()
    spread_chart = bar_chart(
        [f"{t['ts'][11:19]}" for t in ticks if t["instrument"] == "EURUSD"],
        [round(t["spread"] * 100000, 1) for t in ticks if t["instrument"] == "EURUSD"],
        unit=" pip",
        places=1,
        caption="EURUSD fixture spreads in pips - the negative bar is the deliberate crossed quote",
    )
    return templates.TemplateResponse(
        request,
        "phase0.html",
        ctx(
            active=0,
            results=results,
            families=families,
            revised=live.revised_period_count(),
            spread_chart=spread_chart,
            macro=live.macro_fixture(),
            bars=live.bars_fixture(),
        ),
    )


@app.get("/phase/1", response_class=HTMLResponse)
def phase1(request: Request):
    """Phase 1 reads real ingest state.

    The demo generators this route used to call were deleted when the pipeline
    landed, per docs/ui-design.md §9 — a demo generator left in place after its
    panel goes live is what the next person reaches for by mistake.
    """
    summary = live.ingest_summary()
    store_stats = live.tick_store_stats()

    ticks_chart = None
    if store_stats:
        ticks_chart = bar_chart(
            [r["instrument"] for r in store_stats],
            [float(r["ticks"]) for r in store_stats],
            places=0,
            caption="Ticks ingested into tick_raw, by instrument",
        )

    return templates.TemplateResponse(
        request,
        "phase1.html",
        ctx(
            active=1,
            summary=summary,
            monthly=live.ingest_monthly_coverage(),
            store_stats=store_stats,
            recon=live.ingest_reconciliation(),
            ticks_chart=ticks_chart,
        ),
    )


@app.get("/phase/2", response_class=HTMLResponse)
def phase2(request: Request):
    """Phase 2 reads real flags from tick_flag. The flag_density generator this
    route used to call was deleted when the detectors landed.
    """
    totals_rows = live.flag_totals()
    hour_rows = live.flag_hours()

    totals = None
    if totals_rows:
        agg: dict[str, int] = {}
        for r in totals_rows:
            agg[r["flag"]] = agg.get(r["flag"], 0) + int(r["flags"])
        names = sorted(agg, key=lambda k: -agg[k])
        totals = bar_chart(
            names,
            [float(agg[n]) for n in names],
            places=0,
            caption="Flags written by detector, all instruments",
        )

    density = None
    if hour_rows:
        flags = sorted({r["flag"] for r in hour_rows})
        hours = [f"{h:02d}" for h in range(24)]
        lookup = {(r["flag"], int(r["hour_utc"])): int(r["flags"]) for r in hour_rows}
        matrix = [[float(lookup.get((f, h), 0)) for h in range(24)] for f in flags]
        density = heatmap(
            flags, hours, matrix, caption="Flag counts by detector and hour of day (UTC)"
        )

    return templates.TemplateResponse(
        request,
        "phase2.html",
        ctx(
            active=2,
            totals=totals,
            density=density,
            share=live.flagged_share(),
            catalogue=live.detector_catalogue(),
            bars=live.bar_coverage(),
            bar_rows=live.bar_sample("EURUSD", 8),
            bar_recon=live.bars_reconcile(),
            explained=live.explain_day("EURUSD", "2024-01-08", limit=25),
        ),
    )


@app.get("/phase/3", response_class=HTMLResponse)
def phase3(request: Request):
    """Phase 3 reads the real RTDSM archive and runs as_of() live."""
    timeline = step_timeline(
        live.RECORDED_PAYEMS_VINTAGES,
        places=0,
        unit="k",
        caption="US nonfarm payrolls, reference period January 2024, by vintage",
    )
    return templates.TemplateResponse(
        request,
        "phase3.html",
        ctx(
            active=3,
            timeline=timeline,
            summary=live.macro_summary(),
            revisions=live.macro_revisions(10),
            rebased=live.rebased_series(),
            walkthrough=live.as_of_walkthrough(),
        ),
    )


@app.get("/phase/4", response_class=HTMLResponse)
def phase4(request: Request):
    """Phase 4 reads the real session calendar. The demo generators this route
    used to call were deleted when the calendar landed.
    """
    return templates.TemplateResponse(
        request,
        "phase4.html",
        ctx(
            active=4,
            coverage=live.session_coverage(),
            holidays=live.session_holidays(),
            dst=live.dst_windows(2024),
            dst2025=live.dst_windows(2025),
            moments=live.moment_examples(),
            caveats=live.holiday_caveats(),
        ),
    )


@app.get("/phase/5", response_class=HTMLResponse)
def phase5(request: Request):
    hours, series = demo.spread_by_hour()
    spread = line_chart(
        hours,
        series,
        unit=" pip",
        places=3,
        zero_base=True,
        caption="Median spread by hour of day (UTC)",
    )
    months, rates = demo.cross_feed_reconciliation()
    recon = bar_chart(
        months,
        rates,
        unit=" bp",
        places=1,
        caption="Dukascopy vs HistData disagreement rate by month",
    )
    labels, tickrate = demo.tick_rate_anomalies()
    anomaly = line_chart(
        labels, tickrate, places=0, caption="EURUSD ticks per minute - the trough is a feed gap"
    )
    return templates.TemplateResponse(
        request,
        "phase5.html",
        ctx(
            active=5,
            spread=spread,
            recon=recon,
            anomaly=anomaly,
            series_names=[n for n, _ in series],
        ),
    )


@app.get("/phase/6", response_class=HTMLResponse)
def phase6(request: Request):
    variants = demo.contamination_variants()
    chart = bar_chart(
        [v["variant"].split(" - ")[0] for v in variants],
        [v["sharpe"] for v in variants],
        places=2,
        highlight=0,
        caption="Reported Sharpe by data regime - A is the only honest one",
    )
    return templates.TemplateResponse(
        request,
        "phase6.html",
        ctx(active=6, variants=variants, chart=chart, hypotheses=demo.hypotheses()),
    )


@app.get("/api/health")
def health():
    r = live.run_tests()
    return {
        "containers": live.docker_status(),
        "suite": {"passed": r.passed, "failed": r.failed, "reds_all_expected": r.all_reds_expected},
    }


@app.get("/api/tests", response_class=HTMLResponse)
def rerun_tests(request: Request):
    """HTMX target: re-run the suite and swap the results card back in."""
    return templates.TemplateResponse(
        request,
        "partials/test_results.html",
        ctx(results=live.run_tests(force=True)),
    )
