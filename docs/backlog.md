# Settlement Exception Manager — Ticket Backlog

**Prefix:** `SE-`. One ticket per branch, one PR per ticket.

Build the ticket router first. This project assumes you already have classification, evaluation harnesses, fallbacks and idempotency in your hands.

This backlog carries **Kubernetes** and **MongoDB**, because a multi-service reconciliation system is the honest home for both.

---

## Before you start

### The domain, learned properly

You cannot fake this and you shouldn't try. Two hours of reading before SE-04 saves a week of building the wrong model.

**The lifecycle:** order → execution → allocation (splitting a block across client accounts) → confirmation and affirmation (both sides agree the economics) → settlement instruction to the custodian or CSD → matching at the CSD → settlement, delivery versus payment. If it doesn't settle by the intended date it is a **fail**, and penalties accrue daily.

**The vocabulary you must use correctly:** SSI, nostro, DvP and FoP, ISIN, LEI, CSD, custodian, intended settlement date, actual settlement date, partial settlement, STP rate, fail rate, break ageing.

**Read:** the UK Accelerated Settlement Taskforce implementation plan, the EU T+1 Industry Committee roadmap, and ESMA's CSDR settlement discipline material. These are the actual source documents, they are free, and they tell you what the industry thinks the problems are.

### Technical prerequisites

**`Decimal`, always.** Quantities and money never touch a float. Set explicit rounding on every calculation, and decide rounding direction deliberately — in settlements it is a business decision, not a formatting one.

**ISO 15022 / ISO 20022** basics before SE-08. You are implementing a simplified parser, not a full stack, but the field structure should be recognisable to someone who works with the real thing.

**Kubernetes on `kind` locally** before SE-30. Do not start on a managed cluster.

---

## Sprint 0 — Scaffolding

**SE-01 · Repository scaffold** — reuse the router setup: pyproject, ruff, mypy strict, pytest, pre-commit, the package layout from the README.

**SE-02 · Compose environment** — app, PostgreSQL, Redis, MongoDB, with healthchecks. Money columns as `NUMERIC`, never `DOUBLE PRECISION`.

**SE-03 · CI pipeline** — lint, types, tests, build, blocking on PRs.

---

## Sprint 1 — The deterministic core (no AI)

This sprint is the project. Everything credible about it comes from here.

**SE-04 · Domain models**
Build: `Trade`, `Allocation`, `SettlementInstruction`, `StatementLine`, `Party`, `SSI`, `Break`, `Resolution`, `AuditEvent`.
Constraints: `Decimal` for quantity and consideration; ISIN validated including its check digit; LEI format validated; every `Break` carries the two sides being compared, the disagreeing fields, and its intended settlement date.
Done when: constructible in tests with nothing else imported; a `Break` without evidence fails construction; a malformed ISIN raises.
Teaches: encoding domain rules in types. The ISIN check digit is a small thing that signals you looked at the real spec.

**SE-05 · Trade lifecycle state machine**
Build: `domain/lifecycle.py`. Legal transitions only, per the README diagram, including partial settlement and the failed path. Every transition returns an `AuditEvent`.
Done when: parametrised tests cover every legal transition and reject every illegal one; no code path anywhere can set a state directly.
Teaches: the same discipline as the loan state machine, in a domain where getting it wrong has regulatory consequences.

**SE-06 · Penalty accrual**
Build: `domain/penalties.py` — daily accrual on a failed amount from intended settlement date, parameterised by rate and asset class, with explicit rounding.
Done when: unit tested against figures you computed by hand, including same-day resolution, weekend and holiday handling, and partial settlement reducing the failed amount.
Teaches: business-day calendars, which are a real and underestimated source of bugs.

**SE-07 · Synthetic data generator**
Build: `generation/` — a configurable book of trades and a matching custodian statement, with break types injected at configurable rates. Seeded, so runs are reproducible.
Done when: `python -m settlement.generation --trades 5000 --seed 42` produces a book, a statement, and a manifest of exactly which breaks were injected where.
Teaches: the data model, from the inside. The manifest is your golden set in Sprint 4, so build it properly now.

---

## Sprint 2 — Reconciliation

**SE-08 · Statement ingestion**
Build: parsers for the internal book feed and a custodian statement, plus a simplified ISO 15022 message parser for instruction and status messages.
Done when: malformed input produces a clear domain error naming the field and line, never a silent skip.
Teaches: message standards, and defensive parsing of files you did not produce.

**SE-09 · Economic key matching**
Build: `domain/matching.py` — the exact-match pass on ISIN, quantity, settlement date, counterparty and direction. Pure function, no I/O.
Done when: unit tested including duplicates on both sides, one-to-many, and many-to-one.
Teaches: that reconciliation is a set-matching problem before it is anything else.

**SE-10 · Tolerance evaluation**
Build: tolerances as versioned YAML configuration — absolute and percentage, per currency and instrument type. A match within tolerance is matched, with the difference recorded. The tolerance set version is stamped on every result.
Done when: a historical break remains explicable under the tolerance set in force when it was raised.
Teaches: configuration as data with versioning, which is the same lesson as policy-as-data in the lending version.

**SE-11 · Fuzzy pass and break creation**
Build: near-match detection for records that fail exact matching, producing a candidate pair and a typed `Break` rather than a guess.
Done when: the generator's manifest is used to measure match rate and false break rate; both recorded as the baseline.
Teaches: that the expensive error is the false break, not the missed match.

**SE-12 · Three-way reconciliation**
Build: extend to book versus custodian versus CSD status, so a break can be localised to a leg.
Done when: a break present between book and custodian but absent between custodian and CSD is correctly attributed.
Teaches: why real operations teams reconcile more than two sources.

---

## Sprint 3 — Prioritisation and controls

**SE-13 · Ageing and prioritisation**
Build: `domain/ageing.py` — time to intended settlement date, business-day aware, with escalation bands and penalty exposure as a tiebreak.
Done when: a break two hours from deadline outranks a larger break two days out, and the rule is unit tested.
Teaches: the T+1 argument made concrete. This is the feature the whole business case rests on.

**SE-14 · Maker-checker and segregation**
Build: `domain/controls.py` — pure functions enforcing that a proposer cannot approve, that amendments require a distinct approver, and that overrides require a reason code from a fixed list.
Done when: attempting self-approval raises a domain error; tested exhaustively.
Teaches: financial controls implemented in the domain rather than assumed in the UI. Very few portfolio projects have this and it reads as genuine industry awareness.

**SE-15 · Immutable audit store**
Build: MongoDB append-only event documents. Every state transition, match decision, classification, proposal and human action. Plus a point-in-time query returning the state of any trade as at a timestamp.
Done when: state can be reconstructed from events alone, and an attempt to update an existing event document is rejected.
Teaches: event sourcing, and the document-database use case that is actually a document-database use case.

---

## Sprint 4 — Evaluation, before any agent

**SE-16 · Golden break set**
Build: the generator's manifest as ground truth, plus 20 to 30 hand-written hard cases: two plausible causes at once, one break that is really two, a break resolved by a corporate action, and clean trades that must produce nothing.
Done when: versioned, loadable, with the rationale for each hand-written case documented.

**SE-17 · Metrics module**
Build: match rate, false break rate, classification accuracy per break type, prioritisation quality, resolution acceptance rate, cost per break.
Done when: unit tested against a hand-computed example.

**SE-18 · Evaluation runner and baseline**
Build: a CLI running any configuration over the golden set, writing timestamped reports.
Done when: the deterministic-only pipeline has a recorded baseline. Every later change is judged against it.

---

## Sprint 5 — The agents

**SE-19 · Agent base**
Build: structured output, timeout, bounded retry, one re-ask on invalid output, token and cost accounting, prompts in versioned files with the version stamped on every output.
Done when: malformed output raises a clear domain error; every call is costed and attributed.

**SE-20 · Break classifier**
Build: `agents/classifier.py` — break in, root cause plus cited evidence fields out. Constrained to the taxonomy; no free-text categories.
Done when: scored per break type against the golden set; a classification citing a field not present in the break is a test failure.
Teaches: grounding, and why a constrained taxonomy beats open-ended labelling in a controlled environment.

**SE-21 · Investigator**
Build: gathers related records — the counterparty's other breaks today, the SSI history for that account, recent static data changes — and explains the likely cause in context.
Done when: an SSI break caused by a recent static data change is linked to that change.
Teaches: retrieval as context for judgment, without calling it RAG.

**SE-22 · Resolution drafter**
Build: proposes a resolution from a fixed action set (amend SSI, re-instruct, cancel and rebook, chase counterparty, escalate) and drafts the counterparty query.
Constraint: it proposes. It never applies.
Done when: every proposal traces to break evidence; unsupported claims fail the test.

---

## Sprint 6 — The graph

**SE-23 · State schema**
Build: typed state per break investigation — break, related records, classification, proposal, draft query, audit events (append reducer), tokens spent, status.
Done when: every field documented with its reducer rationale.

**SE-24 · Pipeline graph**
Build: nodes for classify, investigate, propose and draft; Postgres checkpointer from the start.
Done when: runs end to end on a golden break; checkpoints inspectable after each node.

**SE-25 · Deterministic supervisor**
Build: conditional edges routing on knowable facts — a `STATIC_DATA` break skips investigation and goes straight to the data team queue; a break under a confidence threshold goes straight to a human; a break past its deadline escalates immediately.
Constraint: pure Python, no model call.
Done when: every branch is covered by a test and every routing decision is audited.

**SE-26 · Budgets, breaker and locks**
Build: token ceiling per break in Redis, node visit limits, circuit breaker on repeated model failures, and a Redis lock so two workers cannot investigate the same break.
Done when: a concurrent test proves one worker proceeds and the other is rejected cleanly.

---

## Sprint 7 — The human in the loop

**SE-27 · Approval gate**
Build: a node using `interrupt` before any resolution is applied. Graph pauses and persists.
Done when: the process can be killed entirely and the investigation survives.

**SE-28 · Resume and four-eyes**
Build: an API to resume with an operator's decision — approve, reject with reason code, or amend the proposal. The controls from SE-14 are enforced here, so a self-approval attempt is rejected at the domain layer.
Done when: a break paused overnight, container restarted, resumes correctly; self-approval is impossible.
Teaches: the control that makes this system plausible to anyone who has worked in operations.

**SE-29 · Operator corrections feed evaluation**
Build: rejected classifications and amended proposals are captured with the operator's reason and exported as new golden cases.
Done when: a rejection appears in the next evaluation run.

---

## Sprint 8 — Kubernetes

**SE-30 · Local cluster**
Build: `kind` cluster, manifests for three services — API, matching worker, agent worker — plus ConfigMaps, Secrets, liveness and readiness probes, and resource requests and limits.
Constraint: liveness and readiness must differ and you must be able to say why.
Done when: killing the agent worker mid-investigation loses no work, and the matcher keeps running.
Teaches: more than a managed cluster would, for free.

**SE-31 · Scaling and resilience**
Build: an HPA on queue depth, a PodDisruptionBudget, and graceful shutdown that drains in-flight work.
Done when: a rolling deploy during an active reconciliation run completes with no lost or duplicated breaks.
Teaches: the difference between running on Kubernetes and running well on it.

---

## Sprint 9 — Observability and finish

**SE-32 · Tracing**
Build: OpenTelemetry spans across ingestion, matching, each agent and the approval. One trace per break, with prompt version, tokens, latency and cost.
Done when: you can open one break and see its full history, including the pause.

**SE-33 · Operations dashboard**
Build: break queue by ageing band, STP rate, fail rate, false break rate, open penalty exposure, mean time to resolve.
Done when: the metrics match what the evaluation harness reports.
Teaches: the numbers an operations manager actually asks about, which is a good interview answer on its own.

**SE-34 · Full evaluation**
Build: run the golden set end to end with agents. Compare against the SE-18 baseline. Record classification accuracy, cost per break, and how often a human overrode the proposal.
Done when: `docs/evaluation.md` states honestly what the agents added, including where they made things worse.

**SE-35 · Documentation and ADRs**
Build: README updated with real numbers, architecture diagram, and the six ADRs listed in the README.
Done when: someone from an operations background could read it and recognise their own job.

---

## The interview answers this produces

By the end you can answer, with evidence from your own system: what happens to exception management under T+1 and why it is the pressure point; why matching must be deterministic and what you would never let a model decide; how you measure a reconciliation system, and why the false break rate matters more than the match rate; what maker-checker is and where you enforced it; and how you would reconstruct the state of any trade at any past moment.

That is a markets technology interview, and almost no graduate candidate can have that conversation.
