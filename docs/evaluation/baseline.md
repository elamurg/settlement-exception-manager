# Baseline: deterministic reconciliation (SE-11)

The first measured result of the full pipeline, before any agent work. **Every later change is
judged against these numbers**, including the `reference` tiebreak, new tolerance rules and the
Sprint 5 agents.

Recorded 2026-10-22 from commit `<commit>` (fill in after committing: `git rev-parse --short HEAD`).

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
| Breaks found with the right type | **84.94%** (440 / 518) |
| Found with the wrong type | 3 |
| Missed (left unresolved) | 75 |

| Break type | Injected | Found | Wrong type | Missed |
|---|---|---|---|---|
| PRICE_BREAK | 47 | 47 | 0 | 0 |
| DUPLICATE_BOOKING | 29 | 29 | 0 | 0 |
| SSI_MISMATCH | 153 | 152 | 0 | 1 |
| DATE_MISMATCH | 49 | 40 | 0 | 9 |
| UNMATCHED_INSTRUCTION | 51 | 41 | 2 | 8 |
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
cases. The third wrong type is a `STATIC_DATA` break (T-002617) reported as
`UNMATCHED_INSTRUCTION`: its wrong-LEI line wasn't recognised as a near match.

**Most misses are `STATIC_DATA` (47 of 75).** A wrong counterparty LEI often leaves the
statement line one difference away from several trades, or leaves a trade with several candidate
lines. The unique-both-ways rule refuses to pick, so they're left unresolved. The 10
`QUANTITY_BREAK` and 9 `DATE_MISMATCH` misses are the same effect.

**8 `UNMATCHED_INSTRUCTION` misses include 2 deliberate ones.** A trade with no near candidate is
left unresolved, instead of being reported as having "no matching line", when a leftover line
shares its ISIN, side and counterparty: that line is probably ours with more than one field
wrong, and reporting it as missing would send ops chasing the custodian for an instruction that
exists. In this data that happens twice, by coincidence, to genuinely unmatched trades. A looser
rule (any leftover line with the same ISIN and side) was measured and rejected: it would have
moved 42 of the 43 correctly found unmatched instructions to unresolved.

**Price, duplicate and SSI breaks are found almost perfectly**, because they're decided on a
matched pair, where there's nothing ambiguous left.

**Every clean pair in this data matched with a difference of exactly zero.** The generator only
changes consideration for injected price breaks (0.1–2%, always outside tolerance), so the
tolerance rules accept no non-zero differences here. The report now keeps each clean pair's
`ToleranceResult`, so accepted differences will be measurable once the generator adds small
consideration noise.

## Changes made during review

These numbers include the SE-10 and SE-11 review fixes. Compared with the first run
(442 found, 73 missed), the only change is the 2 deliberate `UNMATCHED_INSTRUCTION` cases above.
The other fixes left the numbers unchanged:
- duplicate keys in a tolerance YAML file are now rejected (no effect on valid files);
- a matched pair with no SSI on file is now a `STATIC_DATA` break (the engine always has the SSI
  from the book, so no effect here);
- the near-match search only compares lines with the same ISIN and side (faster, same results);
- clean pairs keep their tolerance results in the report (same counts).

## What to try next

1. **Use `reference` as a tiebreak**, only to choose between several near candidates or inside
   an ambiguous group, never as the primary key, since real references are often missing or
   wrong. Expected: most of the 47 `STATIC_DATA` misses recovered, plus the 2 false breaks above.
   Wait for the SE-07 follow-up that makes some generated references missing or wrong, so the
   before/after is honest.
2. **Add hard cases to the golden set (SE-16):** a line that differs from its trade in two
   fields, which this generator never produces, plus trades sharing an economic key by
   coincidence.
3. **Small consideration noise in the generator**, so within-tolerance differences appear in
   the data and the tolerance rules are exercised on clean trades too.
4. **Decide the open question from SE-11a:** a break type for "the custodian reports a trade we
   never booked" (currently left unresolved).
5. **Instrument types for tolerances** (SE-10 question). It won't change these numbers until
   `v1.yaml` has instrument-specific rules.

## Updating this baseline

This file only changes in a reviewed PR. When the pipeline changes, run the command again, add
the new numbers as a new section (keep this one for comparison), and record what changed and
why.
