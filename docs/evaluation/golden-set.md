# Golden break set (SE-16)

The fixed exam every change to the pipeline is marked against: the same questions every time, with a known right answer for each. It lives in [`golden/v1/golden.yaml`](../../golden/v1/golden.yaml) and is loaded by `settlement.evaluation.golden.load_golden_set()`.

## What's in it

**The generated part.** 5,000 trades, seed 42. The SE-07 generator is reproducible, so those two numbers plus its manifest are the answer key for thousands of ordinary, one-field breaks. That part is already scored by [`baseline.md`](baseline.md).

**21 hand-written hard cases.** The generator only makes easy breaks: one field wrong at a time. These are the messy situations it can't make, in the four kinds the ticket asks for:

| Kind | Cases | What it tests |
|---|---|---|
| `MUST_BE_CLEAN` | 9 | traps for false alarms: the expensive error |
| `CORPORATE_ACTION` | 4 | the company changed its shares; nobody made a mistake |
| `REALLY_TWO` | 4 | one trade, two separate problems, both must be reported |
| `TWO_CAUSES` | 4 | could be either cause, so a human must decide |

Every case records the **right** answer, not what the engine currently does, and a rationale explaining why.

## Today's engine: 11 of 21

Exact match → tolerance (v1) → fuzzy pass, as on `main` at `4f7ce32`.

| Case | Kind | Right answer | Engine today |
|---|---|---|---|
| `two-fields-off` | TWO_CAUSES | T-1 unresolved | ✅ |
| `stock-split` | CORPORATE_ACTION | CORPORATE_ACTION T-1 | ❌ QUANTITY_BREAK T-1 |
| `within-tolerance` | MUST_BE_CLEAN | T-1 clean | ✅ |
| `small-trade-absolute-floor` | MUST_BE_CLEAN | T-1 clean | ✅ |
| `same-security-different-trades` | MUST_BE_CLEAN | T-1 clean, T-2 clean | ✅ |
| `identical-twins-each-matched` | MUST_BE_CLEAN | T-1 clean, T-2 clean | ❌ T-1 unresolved, T-2 unresolved |
| `lowercase-bic` | MUST_BE_CLEAN | T-1 clean | ✅ |
| `free-of-payment` | MUST_BE_CLEAN | T-1 clean | ✅ |
| `ssi-and-price` | REALLY_TWO | PRICE_BREAK T-1, SSI_MISMATCH T-1 | ✅ |
| `near-match-hides-ssi` | REALLY_TWO | DATE_MISMATCH T-1, SSI_MISMATCH T-1 | ❌ DATE_MISMATCH T-1 |
| `duplicate-with-price-difference` | REALLY_TWO | DUPLICATE_BOOKING T-1, DUPLICATE_BOOKING T-2, PRICE_BREAK T-1 | ❌ DUPLICATE_BOOKING T-1, DUPLICATE_BOOKING T-2 |
| `lei-and-quantity` | TWO_CAUSES | T-1 unresolved | ❌ UNMATCHED_INSTRUCTION T-1 |
| `one-line-two-candidates` | TWO_CAUSES | T-1 unresolved, T-2 unresolved | ✅ |
| `opposite-direction` | TWO_CAUSES | T-1 unresolved | ❌ UNMATCHED_INSTRUCTION T-1 |
| `reverse-split` | CORPORATE_ACTION | CORPORATE_ACTION T-1 | ❌ QUANTITY_BREAK T-1 |
| `bonus-issue` | CORPORATE_ACTION | CORPORATE_ACTION T-1 | ❌ QUANTITY_BREAK T-1 |
| `isin-change` | CORPORATE_ACTION | CORPORATE_ACTION T-1 | ❌ UNMATCHED_INSTRUCTION T-1 |
| `split-and-price-off` | REALLY_TWO | CORPORATE_ACTION T-1, PRICE_BREAK T-1 | ❌ QUANTITY_BREAK T-1 |
| `friday-to-monday` | MUST_BE_CLEAN | T-1 clean | ✅ |
| `gbp-within-tolerance` | MUST_BE_CLEAN | T-1 clean | ✅ |
| `reference-missing-but-unique` | MUST_BE_CLEAN | T-1 clean | ✅ |

## What the failures say

**False alarms are well controlled.** 8 of the 9 clean traps pass, and the 9th (`identical-twins-each-matched`) is left for a human rather than flagged as a break, so it costs match rate, not trust.

The ten failures come from four gaps, each a candidate ticket:

1. **No corporate-action handling** (5 cases). A split, reverse split or bonus issue changes the quantity but not the value; the engine reads it as a quantity break. An ISIN change reads as "missing".
2. **"Missing" when a line exists** (2 cases). When the custodian's line differs in the counterparty or the direction as well, the trade is reported as `UNMATCHED_INSTRUCTION`. A line that could be ours should send the trade to a human, especially a possible direction error.
3. **A second problem hides behind the first** (2 cases). SSI details are only checked on exact matches, and amounts aren't checked on duplicate groups, so the second break goes unreported.
4. **Identical trades can't be told apart** (1 case). The `reference` tiebreak discussed in SE-09/SE-11 would fix `identical-twins-each-matched`.

When one of these is fixed, its cases flip to ✅, and the clean traps make sure the fix didn't add false alarms.

## Adding a case

Each case starts from a standard trade (`STANDARD_TRADE` in `golden.py`) and only writes what differs; each statement line starts as a perfect match for the trade it references. The header of `golden.yaml` explains every field. The loader is strict: an unknown field, an unquoted value, a trade missing from the expected answer (or in two outcomes), or a missing rationale is an error naming the case and field.

**Never edit a released version.** Copy `golden/v1/` to `golden/v2/` and change that, so results stay comparable over time.

## What comes next

- **SE-17 (metrics):** turn "11 of 21" into proper measures: per kind, per break type, false breaks on the clean traps.
- **SE-18 (evaluation runner):** run any configuration over the generated part and these cases together, and write timestamped reports. The table above was produced by hand with `reconcile()`; the runner will make it repeatable.
