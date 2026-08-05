# UI design — pattern and approach

The dashboard covers all seven phases. Phase 0 is built, so its panels show
real output; phases 1–6 do not exist yet, so their panels show demo data. The
central design problem is therefore **not layout — it is honesty**: how to
present a mostly-unbuilt system without any screen implying more than is true.

Everything below follows from that.

---

## 1. Framework: FastAPI, not Django

Django was the obvious suggestion and it is the wrong choice here, for one
specific reason.

This project's first success criterion is:

> Every read path goes through `as_of(t)` — no direct table SELECTs outside the
> query layer.

Django's centre of gravity is an ORM plus an auto-generated admin, and both
exist to do exactly the thing the invariant forbids: query tables directly.
Adopting Django would mean either fighting the framework on every screen or
quietly letting `Model.objects.filter(...)` become a second read path. The
second outcome is the more likely one, and it is precisely how look-ahead bias
re-enters a system that had eliminated it. A framework should make the correct
thing easy; Django would make the incorrect thing easy.

FastAPI carries no ORM. The UI can only reach data through
`fxpit.query`, which makes the architectural rule a structural property
rather than a code-review convention.

| | Verdict |
|---|---|
| **FastAPI + Jinja2** | **Chosen.** No ORM, so no second read path. Server-rendered HTML, no build step, free OpenAPI docs at `/api/docs` |
| Django | Rejected — ORM and admin generate direct table access, contradicting success criterion #1 |
| Streamlit / Dash | Rejected — fast to build, but the output is a notebook-grade tool, not a production UI, and layout control is poor |
| React SPA + API | Rejected — best-in-class result, but needs Node, a build pipeline, and a second language for a dashboard that is 95% static reporting |

Interactivity is one button (re-run the suite). That did not justify a
charting or hypermedia library, so `static/htmx.min.js` is a ~40-line shim
implementing only `hx-get` / `hx-target` / `hx-swap` / `hx-indicator`. If the
interactive surface grows, vendor real HTMX rather than extending the shim.

### No external requests, anywhere

The project's defining constraint is that everything is free and requires no
account. The dashboard inherits it: no CDN, no web fonts, no chart library, no
telemetry. It renders with the venv and nothing else. This is also why charts
are **server-rendered SVG** rather than a JS library — see §4.

---

## 2. The core pattern: provenance is a first-class type

Most of this dashboard shows data that does not exist yet. A dashboard that
renders invented numbers in the same visual register as measured ones is
committing the project's own cardinal sin one layer up — presenting
unavailable information as available.

So provenance is not a footnote. It is a required constructor argument.

```python
class Provenance(Enum):
    LIVE      # measured from this repository at request time
    RECORDED  # real measurement from a verified source, captured at a stated instant
    DEMO      # synthetic; describes nothing
```

Three enforcement points, deliberately redundant:

1. **`Panel` has no default provenance.** Constructing one without saying where
   the numbers came from is a `TypeError`.
2. **A DEMO panel must name its replacement.** `Panel.__post_init__` raises if
   `unblocked_by` is empty — *"Unattributed demo data is how a mock-up gets
   mistaken for a result."*
3. **The badge is rendered by the layout macro, not by each template.** There is
   no way to author a card that skips it.

### Three levels, three visual registers

| | Badge | Card treatment | Meaning |
|---|---|---|---|
| **Live** | green | normal | Re-reading gives a fresh answer |
| **Recorded** | blue | normal | Real, but a snapshot — it can go stale |
| **Demo** | amber | **dashed border + hatched top edge** | Shape only |

Demo cards are distinguishable at arm's length, before any text is read, and
carry an explicit inline sentence naming the phase that replaces them. The
badge alone would not survive a screenshot being pasted into a slide.

`demo.py` is seeded from a constant so a demo figure never changes between
reloads — a number that drifts on refresh invites someone to read it as live.
The module docstring notes that deleting it is a project milestone, not a
chore.

---

## 3. Information architecture

One page per phase, plus an overview. Phase pages share a fixed spine:

1. **What this phase does** — plain prose, no jargon
2. **Exit criterion** — quoted from `planning.md`, with a status pill
3. **Panels** — charts and tables, each provenance-badged
4. **Why it is built this way** — the invariant or gotcha the phase turns on

The last section is the one that makes this a research dashboard rather than a
status board. Phase 2 explains why flags live in a separate table; Phase 3
explains why `known_at` is `TIMESTAMPTZ`; Phase 6 states plainly that it will
not produce a profitable strategy. Someone reading only the UI should come away
understanding the project's reasoning, not just its progress.

---

## 4. Charts

Charts are **generated as SVG strings in Python**. Three reasons, in order of
weight:

1. No CDN and no build step — consistent with the no-external-requests rule.
2. Colour is expressed as CSS custom properties, so light/dark is a variable
   swap, not a re-render.
3. **The chart and its table-view twin come from the same call**, so the
   accessible equivalent cannot drift from the picture.

### Palette

Slots 1–3 of the reference categorical palette, validated with the method's own
script rather than by eye:

```
node scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a" --mode light --pairs all
node scripts/validate_palette.js "#3987e5,#d95926,#199e70" --mode dark  --pairs all
```

Both pass all-pairs: worst CVD ΔE **9.2 light / 9.4 dark** (≥8 target), worst
normal-vision ΔE **24.0 / 20.9** (≥15 floor).

Light mode returns one **contrast WARN** — aqua `#1baf7a` at 2.74:1 against the
surface. That is not dismissable; it obligates relief. The mitigation is
structural: **`TableView` is a required field on every chart**, so no chart can
regress out of compliance by someone forgetting to add one.

The chart module caps series at three (`MAX_SERIES`) and raises if exceeded,
because past three the palette no longer clears the all-pairs floor — the
fourth slot would put yellow beside orange.

### Rules applied

- **One axis, always.** No dual-axis chart anywhere. Two measures of different
  scale get two charts.
- **Sequential for magnitude** (flag density, coverage): one hue, light→dark,
  never a rainbow. Categorical is reserved for identity.
- **One series → one colour for every bar.** Colouring bars darker-where-bigger
  would double-encode length as hue.
- **Selective direct labels** — the endpoint, the emphasised bar, the anomaly.
  Never a number on every point.
- **Solid hairline gridlines**, never dashed.
- **2px surface gaps between fills**, not borders drawn around marks.
- **Legend for ≥2 series**; a single series is named by the card title.
- **Status colours are reserved.** They appear only where the colour *means*
  good/bad, and always with an icon and a label so meaning never rests on hue.

### The negative-bar case

Worth recording because it was a real bug caught by automated geometry checking
rather than by eye.

The Phase 0 spread chart plots the fixture's tick spreads, and one of them is
the deliberate crossed quote (`bid > ask`) — a **negative** spread. The first
implementation assumed non-negative values and rendered that bar outside the
viewBox.

Clamping it to zero would have been the fast fix and the wrong one: that bar is
the entire reason the panel exists. `bar_chart` now computes a proper zero
baseline and draws negative bars downward, in `--critical` with a mandatory
direct label — a crossed quote genuinely is a status condition, not a series
identity, so a status colour is the correct choice.

---

## 5. Theming

Light and dark are both *selected*, not one flipped from the other. Dark steps
come from the palette's own dark column and were validated against the dark
surface `#1a1a19` independently.

Precedence is deliberate:

```css
:root { /* light */ }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) { /* OS dark */ }
}
:root[data-theme="dark"] { /* explicit toggle wins both ways */ }
```

The `:where()` keeps the media block at zero specificity so an explicit light
choice beats OS-dark, and the `:not()` guard stops OS-dark overriding it. Theme
is stamped on `<html>` by an inline script *before* first paint, so a dark-mode
reader never sees a light flash.

Every component is written against roles (`--text-secondary`, `--series-1`),
never raw hex. Theming is a token swap, not a second stylesheet.

---

## 6. Accessibility

- **Table view on every chart** — required by the type system, not by
  discipline. This is also the relief for the light-mode contrast WARN.
- **Status never by colour alone** — every status pill carries a glyph and a
  word (`✓ healthy`, `! retry queued`, `✕ failed`).
- **Hit targets exceed marks** — 9–11px transparent `<circle>` over line and
  step-chart points; the visible dot is 4px.
- **`role="img"` and `aria-label`** on every SVG; `<title>` on every mark, so
  values are reachable without hover.
- **Semantic landmarks** — real `<nav>`, `<main>`, `<table>` with `<thead>`.
- **`tabular-nums` in table columns only**; display figures use proportional
  numerals, since equal-width digits make large numbers look loose.
- **Refetch holds the previous render** at reduced opacity rather than flashing
  a skeleton — no layout jump.

---

## 7. What was verified, and what was not

Verified automatically:

| Check | Result |
|---|---|
| All 8 pages + 2 API routes return 200 | pass |
| No unrendered template tags or leaked errors | pass |
| Every chart has a table view | pass |
| Every DEMO badge has a matching hatched card | pass |
| All 9 SVGs' elements inside their viewBox | pass (after the negative-bar fix) |
| Palette validated both modes, all-pairs | pass, 1 documented WARN |
| `ruff check` | clean |

**Not verified: the visual eyeball pass.** No browser or screenshot tool was
available in this environment, so nobody has *looked* at the rendered result.
The geometry check above substitutes for the specific failure it would most
likely catch (labels escaping their container), but it cannot judge typography,
rhythm, or whether the thing actually looks good. Open it and check.

---

## 8. Running it

```powershell
pip install -e ".[dev,web]"
uvicorn fxpit.web.app:app --reload --port 8000
```

Then <http://localhost:8000>. Interactive API docs at `/api/docs`.

The dashboard degrades rather than fails: with Docker stopped, the container
panel reports "daemon not running" instead of erroring, and every other page
still renders.

---

## 9. Extending it

**Replacing a demo panel with a real one** is the main future edit, and the
intended sequence is:

1. Add the real accessor to `live.py` — never inline into a route.
2. Change the panel's provenance from `demo` to `live` or `recorded`.
3. Delete the corresponding generator from `demo.py`.

Step 3 is not optional. A demo generator left in place after its panel goes
live is a loaded gun: the next person to add a panel may reach for it.

**Adding a chart type** goes in `charts.py` and must return a `Chart`
(SVG + `TableView`). The signature makes the accessible twin unskippable.

**One deliberate omission.** There is no filter row. The method specifies one
shared filter row above everything it scopes, never per-card filters — but
today no page has two panels that would share a filter. Adding one now would be
building the abstraction before the need. When Phase 1 lands real coverage data,
an instrument/date-range filter belongs above the whole page, scoping every
panel on it.
