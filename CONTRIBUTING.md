# Contributing

Use a topic branch and keep changes scoped. Run `python -m pytest -q` from the checkout before proposing a change. Live model, browser and fault-injection checks are opt-in; see [testing](docs/05-testing/TEST_PLAN.md).

Use synthetic candidate data. Keep credentials, downloaded candidate files, browser profiles and run artifacts out of Git. Never submit applications to real employers or solve CAPTCHAs. Preserve the deterministic policy, snapshot-bound approval and at-most-once submission guarantees described in the [safety model](docs/03-architecture/POLICY_AND_SAFETY.md).

Document changed behavior and measured limitations, and retain the evidence supporting published claims. Review changes before merging.
