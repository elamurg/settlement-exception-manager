# Agent base (SE-19)

Every agent (classifier, investigator, resolution drafter) talks to Claude through one shared
runner, `settlement.agents.runner.run_agent`. It owns everything that can go wrong with a model
call, so the agents themselves stay small.

## What one call does

1. Checks that the model has a price, before paying for anything.
2. Sends the agent's prompt, the input and the JSON schema the answer must fit.
3. Checks the stop reason: a refusal or an early stop raises straight away.
4. Parses the answer into the agent's own type. If it's broken, it re-asks **once**, saying
   what was wrong. If it's still broken, it raises `InvalidAgentOutput`.
5. Writes a `CallRecord` to the ledger for **every** call: successes, re-asks and failures.
6. Returns an `AgentResult`, stamped with the prompt version and fingerprint.

## Adding an agent

```python
prompt = load_prompt(PROMPTS_DIR, "classifier", "v1")  # prompts/classifier/v1.md
SPEC = AgentSpec(
    name="classifier",
    prompt=prompt,
    schema={...},  # JSON schema: "additionalProperties": false on every object
    parse=parse_classification,  # dict -> your type; raise ValueError when the content is wrong
    effort="medium",
)
result = run_agent(SPEC, user_input, subject=break_id, context=context)
```

`parse` must raise `ValueError` (or `InvalidModel`) for a wrong answer. Any other error is
treated as a bug in `parse` and crashes, so we never pay for a re-ask caused by our own code.

## Errors

| Error | Meaning | What the caller should do |
|---|---|---|
| `InvalidAgentOutput` | Wrong twice | Send to a human |
| `AgentRefused` | The model declined | Send to a human |
| `AgentTruncated` | Stopped early, usually `max_tokens` | Raise `max_tokens`, or send to a human |
| `AgentTimeout` | No answer in time, retries included | Try again later |
| `AgentUnavailable` | API down or rate-limited, retries used up | Try again later |
| `PricingError` | The model has no price in `config/pricing/` | Fix the price list |
| `PromptError` | The prompt file is missing or empty | Fix the prompt file |

A 4xx error from the API other than 429 (bad request, wrong key) is not wrapped: it's our bug
or our configuration, and a retry won't fix it.

## Costs

- Prices live in `config/pricing/v1.yaml`, in `Decimal`, dollars per million tokens.
- A model we didn't ask for (the API reporting a dated model id, say) is recorded under its real
  name and priced as the model we asked for, so a paid call is never lost.
- A timeout is recorded at zero cost, because the API returned no usage. If the request reached
  the API before our side gave up, it may still have been billed.

## Testing

Tests never call the real API. `settlement.agents.fake.FakeModelClient` answers with a scripted
list of replies or errors, so a test can say "bad JSON, then good JSON" or "a refusal".
`AnthropicModelClient` is the only module that imports the SDK.

## Known limits

- The re-ask message (`REASK` in `runner.py`) is a prompt kept in code, not in a versioned file.
- Server-side refusal fallback is not turned on: when it fires, a different model answers, and
  the price list would need that model's price first.
