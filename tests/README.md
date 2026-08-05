# Tests

**The suite is green: 116 passed.** See [`SPEC.md`](SPEC.md) for what each acceptance
test means and its failure mode in one sentence.

```powershell
pytest                      # whole suite
pytest -m acceptance        # the four point-in-time families
pytest -m "not integration" # skip tests needing a live stack
```

The acceptance suite was **red by design** from Phase 0 until Phase 3 implemented
`as_of()`. That is no longer the expected state: a red acceptance test now means a
point-in-time guarantee has regressed.

Do not fix one by weakening its assertion. Phase 0's test bodies have never been
edited — `conftest.py` supplies the backing store they always assumed, and that
separation is what makes them evidence rather than description.
