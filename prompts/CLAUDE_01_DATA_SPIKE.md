# Claude Prompt 01 — Data Spike

You are the data-engineering analyst for SIH26102 MPLADS.

READ FIRST:
- docs/MVP_SPEC.md
- docs/DATA_DICTIONARY.md
- docs/ARCHITECTURE.md
- docs/AI_RULES.md

TASK:
Analyze the actual MPLADS dataset supplied to this repository.

DO NOT:
- invent columns
- invent records
- assume unavailable fields
- build the full application yet
- add unnecessary dependencies

PRODUCE:
1. A data-quality report in `docs/DATA_QUALITY_REPORT.md`
2. A fully populated `docs/DATA_DICTIONARY.md`
3. A recommendation of which MVP detectors are actually supportable from the real fields
4. A list of missing/unsafe assumptions that must NOT be made
5. A small cleaned sample under `data/demo/`

CHECK:
- row/column counts
- missingness
- duplicates
- data types
- date validity
- numeric validity
- possible identifiers
- work description fields
- sanction/expenditure fields
- progress/completion fields
- location fields
- agency/authority fields if available

IMPORTANT:
Do not implement the ML detectors yet. This task is only to establish what the data really supports.

FINAL RESPONSE:
- files created/modified
- key findings
- supported detectors
- unsupported detectors
- tests/checks performed
