# Tests

**The suite is RED on purpose.** See [`SPEC.md`](SPEC.md) for what each test
means and its failure mode in one sentence — that document is the Phase 0
deliverable.

```powershell
pytest                 # whole suite: 17 failed, 11 passed
pytest -m acceptance   # the four point-in-time families
```

Every failure is `NotImplementedError` from `fxpit.query.as_of`, which is
unimplemented until Phase 3. The 11 passing tests validate the fixtures
themselves — they prove the test data still contains the pathologies the
detectors must find.

If a failure ever appears that is *not* a `NotImplementedError`, something real
broke; check it before assuming the red is expected.
