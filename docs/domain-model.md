# Domain model

Class diagrams for `src/settlement/domain/`, the target design for SE-04.

**How to read the diagrams**

| Symbol | Meaning |
|---|---|
| Solid arrow `──▶` | The model **holds** the other object inside it (e.g. a `Trade` holds a `Party`) |
| Dotted arrow `┄┄▶` | The model **points to** another model by its id only (e.g. `Allocation.trade_id`) |
| Hollow triangle `──▷` | **Is a kind of** (inheritance) |
| `?` after a type | May be empty (`None`) |
| `"1..*"` | One or more |

---

## 1. Models and how they connect

```mermaid
classDiagram
    direction TB

    class ISIN {
        value: str
    }
    class LEI {
        value: str
    }

    class Party {
        party_id: str
        name: str
        lei: LEI
    }
    class SSI {
        party_id: str
        currency: str
        custodian_bic: str
        safekeeping_account: str
        cash_account: str
        valid_from: date
    }

    class Trade {
        trade_id: str
        isin: ISIN
        side: Side
        quantity: Decimal
        price: Decimal
        consideration: Decimal
        currency: str
        counterparty: Party
        trade_date: date
        intended_settlement_date: date
        settlement_method: SettlementMethod
        status: TradeStatus
    }
    class Allocation {
        allocation_id: str
        trade_id: str
        account: str
        quantity: Decimal
    }
    class SettlementInstruction {
        instruction_id: str
        trade_id: str
        isin: ISIN
        side: Side
        quantity: Decimal
        consideration: Decimal
        currency: str
        ssi: SSI
        intended_settlement_date: date
    }
    class StatementLine {
        line_id: str
        source: str
        isin: ISIN
        side: Side
        quantity: Decimal
        consideration: Decimal
        currency: str
        counterparty_lei: LEI
        intended_settlement_date: date
        reference: str
    }

    class FieldDifference {
        field: str
        ours: str
        theirs: str
    }
    class Break {
        break_id: str
        break_type: BreakType
        ours: Trade or SettlementInstruction?
        theirs: StatementLine?
        evidence: tuple~FieldDifference~
        intended_settlement_date: date
        raised_at: datetime
        confidence: Decimal?
        prompt_version: str?
    }
    class Resolution {
        resolution_id: str
        break_id: str
        action: ResolutionAction
        rationale: str
        proposed_by: str
        status: ResolutionStatus
        approved_by: str?
        reason_code: str?
    }
    class AuditEvent {
        event_id: str
        entity_type: str
        entity_id: str
        event_type: str
        actor: str
        occurred_at: datetime
        before: str?
        after: str?
    }

    Party --> LEI : lei
    SSI ..> Party : party_id

    Trade --> ISIN : isin
    Trade --> Party : counterparty
    Allocation ..> Trade : trade_id

    SettlementInstruction --> ISIN : isin
    SettlementInstruction --> SSI : ssi
    SettlementInstruction ..> Trade : trade_id

    StatementLine --> ISIN : isin
    StatementLine --> LEI : counterparty_lei

    Break --> "0..1" Trade : ours
    Break --> "0..1" SettlementInstruction : ours
    Break --> "0..1" StatementLine : theirs
    Break --> "1..*" FieldDifference : evidence

    Resolution ..> Break : break_id
```

**In words:**

- **Reference data:** a `Party` is a company we trade with, identified by its `LEI`. An `SSI` holds that party's bank and account details for one currency.
- **Our side:** a `Trade` is what we booked. `Allocation`s split it across client accounts. A `SettlementInstruction` tells the custodian how to settle it, using an `SSI`.
- **Their side:** a `StatementLine` is what the custodian says happened.
- **The problem:** a `Break` compares our side (`ours`) with their side (`theirs`). Its `evidence` lists every `FieldDifference`, meaning each field where the two disagree. A `Break` without evidence can't be created.
- **The fix:** a `Resolution` proposes an action for one `Break`, and a human approves or rejects it.
- **The record:** an `AuditEvent` records any change to any model. `entity_type` and `entity_id` say which one, so it isn't linked to a single class in the diagram.

---

## 2. Enums: the fixed lists of allowed values

```mermaid
classDiagram
    direction LR

    class Side {
        <<enumeration>>
        BUY
        SELL
    }
    class SettlementMethod {
        <<enumeration>>
        DVP
        FOP
    }
    class TradeStatus {
        <<enumeration>>
        BOOKED
        ALLOCATED
        CONFIRMED
        AFFIRMED
        INSTRUCTED
        MATCHED
        SETTLED
        PARTIALLY_SETTLED
        FAILED
        RESOLVED
        CANCELLED
    }
    class BreakType {
        <<enumeration>>
        SSI_MISMATCH
        QUANTITY_BREAK
        PRICE_BREAK
        DATE_MISMATCH
        MISSING_CONFIRMATION
        UNMATCHED_INSTRUCTION
        DUPLICATE_BOOKING
        FUNDING_SHORTFALL
        STATIC_DATA
    }
    class ResolutionAction {
        <<enumeration>>
        AMEND_SSI
        REINSTRUCT
        CANCEL_AND_REBOOK
        CHASE_COUNTERPARTY
        ESCALATE
    }
    class ResolutionStatus {
        <<enumeration>>
        PROPOSED
        APPROVED
        REJECTED
        AMENDED
    }
```

| Enum | Used by |
|---|---|
| `Side` | `Trade`, `SettlementInstruction`, `StatementLine` |
| `SettlementMethod` | `Trade` |
| `TradeStatus` | `Trade` (changed only by `lifecycle.py`, SE-05) |
| `BreakType` | `Break` |
| `ResolutionAction` | `Resolution` |
| `ResolutionStatus` | `Resolution` |

---

## 3. Errors

```mermaid
classDiagram
    direction TB

    class Exception
    class DomainError
    class InvalidIdentifier
    class InvalidModel

    Exception <|-- DomainError
    DomainError <|-- InvalidIdentifier
    DomainError <|-- InvalidModel
```

- `InvalidIdentifier` is raised by `ISIN` and `LEI` when the format or check digit is wrong.
- `InvalidModel` is raised by every other model when one of its rules is broken (a float quantity, an empty evidence list, a naive datetime and so on).
- `except DomainError` catches both.
