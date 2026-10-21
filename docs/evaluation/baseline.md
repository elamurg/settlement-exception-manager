# Baseline: deterministic reconciliation (SE-11)

The first measured result of the full pipeline, before any agent work. **Every later change is
judged against these numbers**, including the `reference` tiebreak, new tolerance rules and the
Sprint 5 agents.

Recorded 2026-10-21 from commit `<commit>` (fill in after committing: `git rev-parse --short HEAD`).

## How to reproduce

```bash
python -m settlement.evaluation.baseline --trades 5000 --seed 42
```

The same seed always gives the same data and the same scores. The run goes through the real
CSV files, so the parsers are part of what's measured.

| Setting | Value |
|---|---|
| Trades | 5,000 (plus 29 duplicate bookings, so 5,029 book rows) |
| Seed | 42 |
| Generator | v1, default break mix (10% of trades) |
| Tolerance set | `config/tolerances/v1.yaml` (v1) |
| Pipeline | exact match on the economic key → tolerance check → fuzzy pass |

## Results

| Metric | Value |
|---|---|
| Match rate | **99.51%** (4,460 / 4,482 clean trades) |
| False break rate | **0.04%** (2 / 4,482 clean trades) |
| Clean trades left unresolved | 20 |
| Breaks found with the right type | **85.33%** (442 / 518) |
| Found with the wrong type | 3 |
| Missed (left unresolved) | 73 |

| Break type | Injected | Found | Wrong type | Missed |
|---|---|---|---|---|
| PRICE_BREAK | 47 | 47 | 0 | 0 |
| DUPLICATE_BOOKING | 29 | 29 | 0 | 0 |
| SSI_MISMATCH | 153 | 152 | 0 | 1 |
| UNMATCHED_INSTRUCTION | 51 | 43 | 2 | 6 |
| DATE_MISMATCH | 49 | 40 | 0 | 9 |
| QUANTITY_BREAK | 48 | 38 | 0 | 10 |
| STATIC_DATA | 141 | 93 | 1 | 47 |

**How the metrics are defined** (see `evaluation/baseline.py`):
- A trade is **clean** if the generator injected nothing on it.
- **Match rate:** clean trades that came out clean (matched, within tolerance, no break).
- **False break rate:** clean trades that got a break anyway. This is the expensive error: noise
  in the ops queue teaches people to stop trusting it.
- **Found:** an injected break where the engine raised a break of the **right type** on that
  trade. A duplicate booking counts once, and is found if any of its bookings is flagged.
- **Wrong type:** the trade got a break, but of a different type. **Missed:** no break at all.

## Analysis

**False breaks are almost zero, by design.** The engine never guesses: a near match must be
unique in both directions, and anything ambiguous is left unresolved for a human. That trades
some missed breaks for a queue that can be trusted.

**The 2 false breaks and 2 of the 3 wrong types are the same situation.** In both cases the
generator happened to create two *different* trades with an identical economic key (same ISIN,
side, quantity, settlement date and counterparty), and one of them had its statement line
removed (an injected `UNMATCHED_INSTRUCTION`). The engine then sees two identical trades and one
line, which looks exactly like a duplicate booking, so it flags both trades as
`DUPLICATE_BOOKING`. From the economic key alone, the two situations can't be told apart:

| Clean trade | Injected trade | Injected break | What the engine raised |
|---|---|---|---|
| T-000339 | T-003714 | UNMATCHED_INSTRUCTION | DUPLICATE_BOOKING on both |
| T-000385 | T-001335 | UNMATCHED_INSTRUCTION | DUPLICATE_BOOKING on both |

The statement line's `reference` names which trade it belongs to, so it would resolve both
cases. The third wrong type is a `STATIC_DATA` break reported as `UNMATCHED_INSTRUCTION`: its
wrong-LEI line wasn't recognised as a near match.

**Most misses are `STATIC_DATA` (47 of 73).** A wrong counterparty LEI often leaves the
statement line one difference away from several trades, or leaves a trade with several candidate
lines. The unique-both-ways rule refuses to pick, so they're left unresolved. The 10
`QUANTITY_BREAK` and 9 `DATE_MISMATCH` misses are the same effect.

**Price, duplicate and SSI breaks are found almost perfectly**, because they're decided on a
matched pair, where there's nothing ambiguous left.

## What to try next

1. **Use `reference` as a tiebreak**, only to choose between several near candidates or inside
   an ambiguous group, never to override the economic key. Expected: most of the 47
   `STATIC_DATA` misses recovered, plus the 2 false breaks above. Measure it against this
   baseline before keeping it.
2. **Decide the open SE-11a questions:** a break type for "the custodian reports a trade we never
   booked", and whether a missing SSI on file should itself be flagged.
3. **Instrument types for tolerances** (SE-10 question). It won't change these numbers until
   `v1.yaml` has instrument-specific rules.

## Updating this baseline

This file only changes in a reviewed PR. When the pipeline changes, run the command again, add
the new numbers as a new section (keep this one for comparison), and record what changed and
why.
