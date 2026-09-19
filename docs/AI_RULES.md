# AI Development Rules

1. Never fabricate MPLADS records, statistics, or validation results.
2. Never state that an anomaly proves fraud.
3. Use only fields present in the inspected dataset.
4. Every risk signal must be explainable.
5. Do not add features outside `MVP_SPEC.md` without an explicit scope decision.
6. Do not introduce dependencies without a reason.
7. Preserve stable API contracts.
8. Add tests for every major detection rule.
9. Keep `main` runnable.
10. Prefer small, reversible changes.
11. Run existing tests before making broad changes.
12. Report files changed, tests run, and known limitations.
