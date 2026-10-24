-- Development database (SQLite). The Azure SQL equivalent, with the least-privilege grants, is schema.azuresql.sql.
CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('Pending', 'Invoiced', 'Completed')),
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL,
    old_status  TEXT NOT NULL,
    new_status  TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    changed_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Append-only. request_id is the primary key so a request can never run twice.
CREATE TABLE IF NOT EXISTS agent_audit (
    request_id      TEXT PRIMARY KEY,
    ticket_id       TEXT NOT NULL,
    runbook_id      TEXT NOT NULL,
    runbook_version INTEGER NOT NULL,
    runbook_hash    TEXT NOT NULL,
    requester_email TEXT NOT NULL,
    order_id        TEXT NOT NULL,
    old_status      TEXT NOT NULL,
    new_status      TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
