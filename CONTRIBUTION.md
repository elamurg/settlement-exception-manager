# Contributing

This project is built by **Ela** and **Tina**. This file covers who owns which ticket and how we work together day to day.

Ticket details are in [docs/backlog.md](docs/backlog.md). The design rules are in [README.md](README.md).

---

## How the work is split

Owning a ticket means you build it. **It doesn't mean nobody else looks at it.** Every PR is reviewed by the other person, so both of us end up knowing the whole system.

## Ticket assignments

### Sprint 0: Scaffolding

| Ticket | Owner | Notes |
|---|---|---|
| SE-01 Repository scaffold | Ela | Do this first. Everything else waits for it. |
| SE-02 Compose environment | Tina | Start after SE-01 is merged. |
| SE-03 CI pipeline | Tina | Start after SE-01 is merged. |

### Sprint 1: The deterministic core

| Ticket | Owner | Notes |
|---|---|---|
| SE-04 Domain models | **Ela + Tina** | Pair on this. Every later ticket uses these models. |
| SE-05 Lifecycle state machine | Ela | |
| SE-06 Penalty accrual | Ela | |
| SE-07 Synthetic data generator | Tina | Its manifest becomes the golden set in SE-16. |

### Sprint 2: Reconciliation

| Ticket | Owner | Notes |
|---|---|---|
| SE-08 Statement ingestion | Tina | |
| SE-09 Economic key matching | Ela | |
| SE-10 Tolerance evaluation | Ela | |
| SE-11 Fuzzy pass and break creation | Ela | Uses Tina's generator manifest to measure the baseline. |
| SE-12 Three-way reconciliation | Tina | Needs SE-09 merged first. |

### Sprint 3: Prioritisation and controls

| Ticket | Owner | Notes |
|---|---|---|
| SE-13 Ageing and prioritisation | Ela | |
| SE-14 Maker-checker and segregation | Ela | |
| SE-15 Immutable audit store | Tina | |

### Sprint 4: Evaluation

| Ticket | Owner | Notes |
|---|---|---|
| SE-16 Golden break set | Tina | Builds on the SE-07 manifest. |
| SE-17 Metrics module | Ela | |
| SE-18 Evaluation runner and baseline | Tina | Needs SE-11 and SE-17. |

### Sprint 5: The agents

| Ticket | Owner | Notes |
|---|---|---|
| SE-19 Agent base | Tina | Do this first. SE-20 to SE-22 build on it. |
| SE-20 Break classifier | Ela | |
| SE-21 Investigator | Tina | Pulls related records from storage. |
| SE-22 Resolution drafter | Ela | |

### Sprint 6: The graph

| Ticket | Owner | Notes |
|---|---|---|
| SE-23 State schema | **Ela + Tina** | Pair on this. It's the contract between every node. |
| SE-24 Pipeline graph | Tina | |
| SE-25 Deterministic supervisor | Ela | |
| SE-26 Budgets, breaker and locks | Tina | |

### Sprint 7: The human in the loop

| Ticket | Owner | Notes |
|---|---|---|
| SE-27 Approval gate | Tina | |
| SE-28 Resume and four-eyes | Ela | Uses the SE-14 controls. Needs SE-27. |
| SE-29 Operator corrections feed evaluation | Ela | |

### Sprint 8: Kubernetes

| Ticket | Owner | Notes |
|---|---|---|
| SE-30 Local cluster | Tina | |
| SE-31 Scaling and resilience | Tina | Ela should review this one closely, to learn Kubernetes. |

### Sprint 9: Observability and finish

| Ticket | Owner | Notes |
|---|---|---|
| SE-32 Tracing | Tina | |
| SE-33 Operations dashboard | Ela | Numbers must match SE-17. |
| SE-34 Full evaluation | Ela | |
| SE-35 Documentation and ADRs | **Ela + Tina** | |

**Totals:** Ela owns 16 tickets, Tina owns 16, and 3 are shared.

If one of us finishes early or gets stuck, swap tickets. Update this table in the same PR so it stays accurate.

---

## Step-by-step: how we collaborate

### One-time setup

1. **Ela** puts the repository on GitHub and adds Tina as a collaborator: *Settings → Collaborators → Add people*.
2. **Ela** protects the `main` branch under *Settings → Branches → Add rule* for `main`:
   - Require a pull request before merging.
   - Require 1 approval.
   - Require status checks to pass (switch this on once SE-03 CI exists).
3. **Tina** accepts the invite and clones the repo:
   ```bash
   git clone <repo-url>
   cd settlement-exception-manager
   ```
4. **Both of us** create a GitHub Issue for each ticket, titled `SE-XX · <name>` and assigned to its owner. Add them to a GitHub Project board with three columns: **To do**, **In progress**, **Done**.
5. **After SE-01 is merged**, both of us set up the local environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   pre-commit install
   ```

### Working on a ticket (every time)

1. **Move the issue** to *In progress* so the other person knows you've started.
2. **Get the latest `main`:**
   ```bash
   git checkout main
   git pull
   ```
3. **Create a branch** named after the ticket:
   ```bash
   git checkout -b se-06-penalty-accrual
   ```
4. **Write code and tests.** Commit small and often, with a clear message:
   ```bash
   git add <files>
   git commit -m "feat(SE-06): accrue penalties on business days only"
   ```
   Prefixes: `feat` (new behaviour), `fix` (bug fix), `test`, `docs`, `refactor`, `chore`.
5. **Run the checks locally** before pushing:
   ```bash
   ruff check .
   mypy src
   pytest
   ```
6. **Push the branch:**
   ```bash
   git push -u origin se-06-penalty-accrual
   ```
7. **Open a pull request** on GitHub:
   - Title: `SE-06 · Penalty accrual`
   - Description: what you built, how you tested it, anything you're unsure about, and `Closes #<issue-number>` so the issue closes on merge.
   - Request a review from the other person.
8. **Respond to review comments** by pushing more commits to the same branch. The PR updates automatically.
9. **Merge** once it's approved and CI is green. Use **Squash and merge** so each ticket is one commit on `main`. Then delete the branch.
10. **Move the issue** to *Done* (this happens automatically if you used `Closes #...`).

### Reviewing the other person's PR

Try to review within one working day, since the other person may be waiting on it. Check:

- [ ] Does it do what the ticket's **"Done when"** says?
- [ ] Are there tests, and do they cover the edge cases the ticket mentions?
- [ ] **No floats** for money or quantities. `Decimal` only, with explicit rounding.
- [ ] `domain/` imports nothing from other packages.
- [ ] No model (AI) call does matching, maths or amendments.
- [ ] Can you understand the code without asking the author? If not, ask for clearer names or a comment.

Choose **Approve** if it's good, or **Request changes** with specific comments. If something is just a suggestion, say so ("nit:") so it doesn't block the merge.

### Keeping your branch up to date

If `main` has moved on while you were working, bring those changes into your branch:

```bash
git checkout main
git pull
git checkout se-06-penalty-accrual
git merge main
```

**If there's a merge conflict,** git marks the conflicting lines in the file with `<<<<<<<`, `=======` and `>>>>>>>`. Edit the file to keep the correct version, delete the markers, then:

```bash
git add <file>
git commit
```

If the conflict is in code the other person wrote, ask them before choosing. Don't guess.

### Working together on shared tickets (SE-04, SE-23, SE-35)

1. Agree on a time and do it together, on a call with screen sharing or side by side.
2. One person types (the "driver") and the other reviews and thinks ahead (the "navigator"). Swap every 30 minutes or so.
3. Use one branch. At the end, the person who didn't push the last commit approves the PR.
4. Add a co-author line to the commit so both names show up:
   ```
   Co-Authored-By: Tina <tina's-github-email>
   ```

### When one ticket depends on the other person's work

- **Agree on the interface first.** If Tina's parser produces `StatementLine` objects that Ela's matcher consumes, decide what `StatementLine` looks like before either of you starts building.
- Until the real code is merged, use a small fake or test fixture in your tests so you aren't blocked.
- Write down any decision that affects both of you as a short note in `docs/adr/`.

### Staying in sync

- **Check-in twice a week** (15 minutes): what I finished, what I'm doing next, what's blocking me.
- **Keep PRs small.** Under about 400 changed lines is easy to review. If a ticket gets big, split it into several PRs.
- **Never push directly to `main`**, and never force-push to a branch the other person is also working on.
- **Ask early.** If you're stuck for more than an hour, message the other person.

---

## Ground rules

These come from the project's design and apply to everyone:

1. A model may read, classify, summarise and draft. **A model may never match, calculate or amend.**
2. `Decimal` everywhere. Floats never touch money or quantities.
3. Every ticket has tests before it merges.
4. Nobody approves their own PR, the same maker-checker rule the system enforces.
