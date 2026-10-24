---
id: get-order-status
version: 1
risk: read
mode: auto
---
# Get order status

## Purpose
Look up the current status of one order that belongs to the requester. Use it for any question about where an order is or what its status is, even when the customer has not given the order number. It is also the check run before a status change.

## Parameters

| name | source | rule |
|---|---|---|
| order_id | ticket | `regex:^ORD-\d{6}$` |
| requester_email | channel | `email` |

## Queries

The ownership check is part of the query: another customer's order returns no row.

```sql name=lookup expect_rows=1
SELECT order_id, status, updated_at
FROM   orders
WHERE  order_id = :order_id
  AND  customer_email = :requester_email
```

## Reply template

```text
Your order {order_id} is currently "{status}" (last updated {updated_at}).
```

## Missing information

```text
Could you send me your order number? It looks like ORD-123456.
```

## Never
- Return another customer's order, or reveal whether it exists.
- Return columns other than the ones selected above.