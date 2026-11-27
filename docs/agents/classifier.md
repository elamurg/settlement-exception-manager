# Break classifier (SE-20)

The engine finds breaks and labels what it can see. The classifier reads each break and
names the root cause, citing the evidence it used. It lives in
[`src/settlement/agents/classifier.py`](../../src/settlement/agents/classifier.py) and runs on
the agent base from SE-19 ([agent-base.md](agent-base.md)).

## Two rules

**Constrained.** The root cause must be a `BreakType`. The schema's `enum` is built from the
enum in `domain/models.py`, so the API cannot return anything else, and the parse function
checks again. No free-text categories: a label nobody defined can't be routed, counted or
audited.

**Grounded.** Every field in `evidence_fields` must be one of the break's own evidence
fields. An answer citing anything else is rejected, the model gets one re-ask naming the
fields it may cite, and a second miss raises `InvalidAgentOutput`. A classification you
can't trace back to the data is a guess.

## What the model sees

The evidence, both sides and the settlement date, as JSON. Not the engine's `break_type`,
and not `break_id`, which contains it (`B-QUANTITY_BREAK-T-1`). Shown either, the model
would copy it and the evaluation would score the engine twice.

## Scoring a prompt

```bash
python -m settlement.evaluation.runner --name classifier-v1 --classifier v1 --limit 20
```

This calls the Claude API and costs money. `--limit N` classifies only the first N records
with breaks, hand-written cases first. The report adds a section scoring the engine's labels
and the classifier's answers on the same records, plus the total cost and cost per break.

To change the prompt, copy `prompts/classifier/v1.md` to `v2.md`, edit that, and compare
the two runs.

## Known limits

- No corporate-action notices reach the classifier, so it is told to prefer
  `QUANTITY_BREAK` over `CORPORATE_ACTION` and lower its confidence. SE-21's investigator
  brings the notices.
- `effort` is `low`. Raise it only if a run shows accuracy needs it.
