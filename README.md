# Settlement Exception Manager

An intraday reconciliation and exception management system for securities settlement. Deterministic matching and lifecycle control at the core, LangGraph agents for classification and resolution drafting, and a human approval gate before anything is amended.

> **Status:** in development. This README describes the target system and is written before the code. Sections marked _(planned)_ aren't built yet.

---

## Why this exists

The UK, EU and Switzerland move to T+1 settlement on 11 October 2027, following North America in May 2024. European post-trade teams that had roughly twelve hours to process a trade will have closer to two, with allocation, confirmation and instruction all compressed onto trade date.

The industry consensus is that exception management is where this breaks. The recommended shift is away from a large overnight reconciliation towards continuous intraday exception handling, where the system clears the standard flow automatically and surfaces only breaks that need human judgment. Between 21% and 30% of securities fails are attributable to settlement instruction data alone.

This system is built for that model: reconcile continuously, classify automatically, prioritise by time-to-deadline, and put a human in front of every amendment.

---

## What it does

1. Ingests trades from the internal book and statements from the custodian.
2. Reconciles them on an economic key, with configurable tolerances.
3. Raises a **break** for anything unmatched or mismatched.
4. Classifies each break by root cause, with evidence.
5. Prioritises by settlement deadline and penalty exposure.
6. Proposes a resolution and drafts the counterparty query.
7. Routes to an operations user, who approves, rejects or amends.
8. Records everything, immutably, under maker-checker control.

It never amends a record on its own. It never releases an instruction. It prepares work for a human and proves what it did.

## What it is not

- Not a settlement engine. It does not instruct the CSD.
- Not a replacement for the ops team. It removes the searching, not the deciding.
- Not a golden source. It reconciles other people's records; it doesn't own them.

---

## The design rule

**A model may read, classify, summarise and draft. A model may never match, calculate, or amend.**

| Concern | How |
|---|---|
| Parsing statements and messages | Deterministic |
| Economic key matching and tolerance evaluation | Deterministic |
| Lifecycle state transitions | Deterministic state machine |
| Fail penalty calculation | Deterministic, `Decimal` |
| Break ageing and prioritisation | Deterministic |
| Root cause classification | Agent, with evidence citation |
| Resolution proposal | Agent, constrained to known break data |
| Counterparty query drafting | Agent |
| The amendment itself | Human, under four-eyes |

If a model is doing arithmetic on a settlement amount, the design is wrong.

---

## Domain model

### Trade lifecycle

```
booked → allocated → confirmed → affirmed → instructed → matched → settled
                                                      ↓
                                              partially_settled
                                                      ↓
                                                   failed → (resolved | cancelled)
```

Transitions are rejected at the domain layer, not the API layer. Every transition is timestamped, attributed and audited.

### Break taxonomy

| Code | Meaning |
|---|---|
| `SSI_MISMATCH` | Settlement instructions differ or are missing |
| `QUANTITY_BREAK` | Quantities disagree beyond tolerance |
| `PRICE_BREAK` | Price or consideration disagrees beyond tolerance |
| `DATE_MISMATCH` | Settlement dates disagree |
| `MISSING_CONFIRMATION` | No counterparty confirmation received by cut-off |
| `UNMATCHED_INSTRUCTION` | Instruction sent, no match at the CSD |
| `DUPLICATE_BOOKING` | Same economic trade booked twice |
| `FUNDING_SHORTFALL` | Insufficient cash or FX not arranged |
| `STATIC_DATA` | Counterparty, account or instrument reference data wrong |
| `CORPORATE_ACTION` | Entitlement or adjustment not reflected |

Each break carries: type, evidence (the specific fields that disagree), the two sides being compared, settlement deadline, ageing, estimated penalty exposure, confidence, and the prompt version that classified it.

### Controls

- **Maker-checker** on every amendment. The proposer cannot approve.
- **Segregation of duties** enforced at the domain layer, not by convention.
- **Reason codes** mandatory on every manual override.
- **Immutable audit log**, append-only, reconstructable to any point in time.
- **Four-eyes** on tolerance changes and on any bulk action.

---

## Architecture

```
src/settlement/
├── domain/                  CORE — pure, no I/O
│   ├── models.py              Trade, Allocation, Instruction, StatementLine,
│   │                          Break, Resolution, AuditEvent, Party, SSI
│   ├── lifecycle.py           the state machine — legal transitions only
│   ├── matching.py            economic key, tolerance evaluation (pure)
│   ├── ageing.py              time-to-deadline, priority, escalation
│   ├── penalties.py           fail penalty accrual — Decimal, explicit rounding
│   └── controls.py            maker-checker and segregation rules
│
├── ingestion/               EDGE
│   ├── book.py                internal trade feed
│   ├── custodian.py           custodian statement parsing
│   └── messages.py            ISO 15022 / ISO 20022 parsing
│
├── reconciliation/          EDGE — orchestrates the pure matcher
│   ├── engine.py              exact pass, tolerance pass, fuzzy pass
│   └── report.py              matched, unmatched, break set
│
├── agents/                  EDGE
│   ├── base.py                structured output, retry, prompt versioning, cost
│   ├── classifier.py          break -> root cause + evidence
│   ├── investigator.py        gathers related records, explains the cause
│   └── drafter.py             resolution proposal and counterparty query
│
├── graph/                   ORCHESTRATION
│   ├── state.py               typed state per break investigation
│   ├── nodes.py
│   ├── supervisor.py          routing — deterministic
│   └── build.py
│
├── storage/                 EDGE — PostgreSQL, Redis, MongoDB audit store
├── evaluation/              golden break set, metrics, regression runner
├── generation/              synthetic trade and statement generator
└── api/                     FastAPI: ingestion, break queue, approvals
```

`domain/` imports nothing from the other packages.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI | Typed contracts, async ingestion |
| Orchestration | LangGraph | Explicit state, deterministic routing, checkpointing |
| Relational | PostgreSQL | Trades, breaks, resolutions — `NUMERIC`, never float |
| Cache / locks | Redis | Idempotency, token budgets, break-level locks |
| Audit store | MongoDB | Append-only event documents, point-in-time reconstruction |
| Models | Anthropic API | Structured output |
| Containers | Docker, Kubernetes | Genuinely multi-service: API, matcher, agent worker |
| Tracing | OpenTelemetry | One trace per break investigation |

---

## Data

There is no real trade data, so the system ships a **synthetic generator** and this is treated as a first-class part of the project rather than test scaffolding.

It produces a book of trades with valid-format ISINs, LEIs, counterparties, settlement dates and currencies, plus a matching custodian statement into which break types are injected at configurable rates. The default distribution weights SSI and static data issues most heavily, reflecting published fail causes.

Being able to explain the break distribution and why it was chosen demonstrates more domain understanding than the classifier does.

---

## Evaluation _(planned)_

**Golden break set.** Generated breaks with known injected causes, plus hand-written hard cases: two plausible causes at once, a break that is actually two breaks, and clean trades that must produce nothing.

**Metrics.**

| Metric | What it measures |
|---|---|
| Match rate | Proportion auto-matched with no break raised |
| False break rate | Matches wrongly flagged — the expensive error |
| Classification accuracy | Root cause correct, per break type |
| Prioritisation quality | Were the breaks nearest to deadline surfaced first |
| Resolution acceptance | Proportion of proposals an operator accepted unchanged |
| Cost | Tokens and money per break |

False break rate matters most. An ops team that stops trusting the queue is worse off than one with no system.

**Regression gate.** CI fails on any degradation past threshold when a prompt or agent changes.

---

## Design decisions

In `docs/adr/`. The significant ones:

1. **Matching is deterministic and tested exhaustively.** Agents never decide whether two records match.
2. **Tolerances are versioned configuration, not code.** A tolerance change is a reviewed config change with an audit record, and historical breaks remain explicable under the tolerance set that produced them.
3. **The supervisor is pure Python.** Routing depends on break type, ageing and confidence, all knowable without a model.
4. **Human approval is unconditional.** No auto-resolution tier, even for high-confidence cases, in v1.
5. **MongoDB for audit, Postgres for state.** Events are append-only documents with a natural point-in-time query; current state is relational.
6. **`Decimal` everywhere.** Floats do not touch money or quantities.

---

## Standards note

Message formats referenced: ISO 15022 MT messages (541 and 543 instructions, 544 to 547 confirmations, 548 status advice, 535 to 537 statements, 950 cash statement) and the ISO 20022 `sese` and `semt` families. Specifications change with annual standards releases — verify against current documentation before implementing parsers rather than trusting this list.

---

## Roadmap

- [ ] Domain core and lifecycle state machine
- [ ] Synthetic data generator
- [ ] Reconciliation engine
- [ ] Evaluation harness and golden break set
- [ ] Classification and investigation agents
- [ ] LangGraph pipeline with deterministic routing
- [ ] Maker-checker approval workflow
- [ ] Kubernetes deployment
- [ ] Tracing and cost controls

## Licence

MIT.
