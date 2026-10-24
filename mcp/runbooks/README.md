# Runbooks

Internal, reviewed procedures for **actions** the agent may perform. A runbook is the only place a database query is allowed to live.

**Never place these files in `docs/`.** `docs/` is the customer-facing knowledge base and is searched to write replies. Runbooks contain SQL and internals.

## Rules

1. One runbook per action. The MCP server builds one tool per runbook. There is no generic "run SQL" tool.
2. Queries are single static statements (`SELECT`, `INSERT` or `UPDATE` only) with named parameters (`:order_id`). Never concatenate values into SQL. `UPDATE` needs a `WHERE` with bound parameters.
3. Every parameter has a source and a rule. Values come from the ticket via the LLM, but code validates them before use, and the tool server validates them again.
4. A write runbook must: use compare-and-set (`WHERE status = :expected_current_status`), set `expect_rows=1` on every query so any other row count rolls everything back, write an `audit` query in the same transaction, and have a `request_id` parameter (the audit table's primary key makes a request run only once).
5. The requester is always the authenticated channel identity (`requester_email`), and ownership (`customer_email = :requester_email`) belongs in the query itself.
6. Runbooks change only through a pull request. The runbook's hash is recorded in every audit row. A malformed runbook stops the server from starting.

## Format

Front matter, then these sections.

```
---
id: <kebab-case, unique>
version: <integer, bump on any change>
risk: read | write
mode: shadow | approval | auto     # default execution mode; writes start with approval
precheck: <runbook id>             # writes only: a read runbook that returns the order's current row
---
# <Title>
## Purpose                (one or two sentences; shown to the LLM so it can choose the action)
## Parameters             (table: name | source | rule)
## Transitions            (writes only; table: from | to | mode)
## Queries                (one ```sql name=<n> expect_rows=<k> block per statement)
## Reply template         (```text block; {placeholders} come from parameters and the returned row)
## Pending message        (writes: text shown to the requester while waiting for approval)
## Missing information    (text asking the requester for missing values)
## Never
```

**Parameter sources:** `ticket` (extracted by the LLM, then validated), `channel` (authenticated identity, currently `requester_email`), `db:<column>` (read from the precheck row), `system` (generated: `request_id`, `ticket_id`, `runbook_id`, `runbook_version`, `runbook_hash`).

**Parameter rules:** `regex:<pattern>`, `enum:a,b,c`, `email`, `any`.

**Conventions for write runbooks:** the parameters `expected_current_status` (source `db:status`) and `new_status` define the transition checked against the Transitions table; the precheck must return a `status` column.

## Index

| Runbook | Risk | Default mode | Notes |
|---|---|---|---|
| [get-order-status](get-order-status.md) | read | auto | the requester's own orders only |
| [set-order-status](set-order-status.md) | write | approval | Pending → Invoiced → Completed, requester's own orders only |