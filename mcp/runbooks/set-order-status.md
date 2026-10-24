---
id: set-order-status
version: 1
risk: write
mode: approval
precheck: get-order-status
---
# Set order status

## Purpose
Use it whenever the customer asks to change, set, mark or move the status of an order (for example to Invoiced or Completed), even if the order number or the new status is missing. It moves one of the requester's own orders to its next status. The agent prepares the change; a human approves it until the evals justify `auto`.

## Parameters

| name | source | rule |
|---|---|---|
| order_id | ticket | `regex:^ORD-\d{6}$` |
| new_status | ticket | `enum:Invoiced,Completed` |
| requester_email | channel | `email` |
| expected_current_status | db:status | `enum:Pending,Invoiced,Completed` |
| request_id | system | `any` |
| ticket_id | system | `any` |
| runbook_id | system | `any` |
| runbook_version | system | `any` |
| runbook_hash | system | `any` |

`expected_current_status` is read from the database by the precheck, never taken from the ticket.

## Transitions

| from | to | mode |
|---|---|---|
| Pending | Invoiced | approval |
| Invoiced | Completed | approval |

Anything not listed (including any change out of `Completed`, or skipping a step) is escalated.

## Queries

One transaction. Every statement must affect exactly one row, otherwise everything is rolled back. `request_id` is the primary key of the audit table, so the same request can never run twice.

```sql name=update expect_rows=1
UPDATE orders
SET    status = :new_status,
       updated_at = CURRENT_TIMESTAMP
WHERE  order_id = :order_id
  AND  status = :expected_current_status
  AND  customer_email = :requester_email
```

```sql name=history expect_rows=1
INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, request_id)
VALUES (:order_id, :expected_current_status, :new_status, :requester_email, :request_id)
```

```sql name=audit expect_rows=1
INSERT INTO agent_audit (request_id, ticket_id, runbook_id, runbook_version, runbook_hash,
                         requester_email, order_id, old_status, new_status, outcome)
VALUES (:request_id, :ticket_id, :runbook_id, :runbook_version, :runbook_hash,
        :requester_email, :order_id, :expected_current_status, :new_status, 'executed')
```

## Reply template

```text
Done: order {order_id} was moved from "{expected_current_status}" to "{new_status}".
```

## Pending message

```text
Thanks, I've prepared your request to move order {order_id} to "{new_status}". Our team will confirm it shortly.
```

## Missing information

```text
To update your order I need the order number (it looks like ORD-123456) and the status you want, Invoiced or Completed.
```

## Never
- Update any column other than `status` and `updated_at`.
- Update more than one order per request, or run without the compare-and-set condition.
- Change an order that does not belong to the requester.
- Accept `expected_current_status` from the ticket text.
- Retry a write automatically after a failure.