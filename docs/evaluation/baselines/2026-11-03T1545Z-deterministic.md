# Evaluation run: deterministic

| Setting | Value |
|---|---|
| Run at | 2026-11-03 15:45 UTC |
| Commit | `6cbf25d` |
| Golden set | v1 (golden/v1/golden.yaml) |
| Generated part | 5000 trades, seed 42, generator v1 |
| Tolerance set | v1 (config/tolerances/v1.yaml) |
| Pipeline | exact match → tolerance → fuzzy pass (deterministic, no agents) |
| Corporate-action notices | not given to the engine: it can't read them yet |

## Scores

| Metric | Generated | Hand-written cases | All |
|---|---|---|---|
| Match rate | 99.51% (4460 / 4482) | 81.82% (9 / 11) | 99.47% (4469 / 4493) |
| False break rate | 0.04% (2 / 4482) | 0.00% (0 / 11) | 0.04% (2 / 4493) |
| Left for a human | n/a (0 / 0) | 60.00% (3 / 5) | 60.00% (3 / 5) |

Prioritisation, resolution acceptance and cost per break: not measured yet. They need
an ideal queue, resolutions and model costs, which come with the agents.

## By break type (all)

| Break type | Precision | Recall |
|---|---|---|
| SSI_MISMATCH | 100.00% (153 / 153) | 98.71% (153 / 155) |
| QUANTITY_BREAK | 90.48% (38 / 42) | 79.17% (38 / 48) |
| PRICE_BREAK | 100.00% (48 / 48) | 96.00% (48 / 50) |
| DATE_MISMATCH | 100.00% (41 / 41) | 82.00% (41 / 50) |
| UNMATCHED_INSTRUCTION | 91.11% (41 / 45) | 80.39% (41 / 51) |
| DUPLICATE_BOOKING | 93.75% (60 / 64) | 100.00% (60 / 60) |
| STATIC_DATA | 100.00% (93 / 93) | 65.96% (93 / 141) |
| CORPORATE_ACTION | n/a (0 / 0) | 0.00% (0 / 5) |

## Hand-written cases: 11 of 21 passed

| Case | Kind | Right answer | Engine | |
|---|---|---|---|---|
| `two-fields-off` | TWO_CAUSES | T-1 unresolved | T-1 unresolved | ✅ |
| `stock-split` | CORPORATE_ACTION | T-1 broken: CORPORATE_ACTION | T-1 broken: QUANTITY_BREAK | ❌ |
| `within-tolerance` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
| `small-trade-absolute-floor` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
| `same-security-different-trades` | MUST_BE_CLEAN | T-1 clean; T-2 clean | T-1 clean; T-2 clean | ✅ |
| `identical-twins-each-matched` | MUST_BE_CLEAN | T-1 clean; T-2 clean | T-1 unresolved; T-2 unresolved | ❌ |
| `lowercase-bic` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
| `free-of-payment` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
| `ssi-and-price` | REALLY_TWO | T-1 broken: PRICE_BREAK, SSI_MISMATCH | T-1 broken: PRICE_BREAK, SSI_MISMATCH | ✅ |
| `near-match-hides-ssi` | REALLY_TWO | T-1 broken: DATE_MISMATCH, SSI_MISMATCH | T-1 broken: DATE_MISMATCH | ❌ |
| `duplicate-with-price-difference` | REALLY_TWO | T-1 broken: DUPLICATE_BOOKING, PRICE_BREAK; T-2 broken: DUPLICATE_BOOKING | T-1 broken: DUPLICATE_BOOKING; T-2 broken: DUPLICATE_BOOKING | ❌ |
| `lei-and-quantity` | TWO_CAUSES | T-1 unresolved | T-1 broken: UNMATCHED_INSTRUCTION | ❌ |
| `one-line-two-candidates` | TWO_CAUSES | T-1 unresolved; T-2 unresolved | T-1 unresolved; T-2 unresolved | ✅ |
| `opposite-direction` | TWO_CAUSES | T-1 unresolved | T-1 broken: UNMATCHED_INSTRUCTION | ❌ |
| `reverse-split` | CORPORATE_ACTION | T-1 broken: CORPORATE_ACTION | T-1 broken: QUANTITY_BREAK | ❌ |
| `bonus-issue` | CORPORATE_ACTION | T-1 broken: CORPORATE_ACTION | T-1 broken: QUANTITY_BREAK | ❌ |
| `isin-change` | CORPORATE_ACTION | T-1 broken: CORPORATE_ACTION | T-1 broken: UNMATCHED_INSTRUCTION | ❌ |
| `split-and-price-off` | REALLY_TWO | T-1 broken: CORPORATE_ACTION, PRICE_BREAK | T-1 broken: QUANTITY_BREAK | ❌ |
| `friday-to-monday` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
| `gbp-within-tolerance` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
| `reference-missing-but-unique` | MUST_BE_CLEAN | T-1 clean | T-1 clean | ✅ |
