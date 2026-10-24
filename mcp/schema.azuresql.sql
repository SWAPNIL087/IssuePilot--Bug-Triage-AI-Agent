-- Azure SQL Database schema, sample data and least-privilege grants for the Managed Identity.
-- Run in the Azure portal Query editor (or sqlcmd) connected to your database as the Entra admin.

CREATE TABLE dbo.orders (
    order_id       NVARCHAR(20)  NOT NULL PRIMARY KEY,
    customer_email NVARCHAR(320) NOT NULL,
    status         NVARCHAR(20)  NOT NULL CHECK (status IN ('Pending', 'Invoiced', 'Completed')),
    updated_at     DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE dbo.order_status_history (
    id          INT IDENTITY(1,1) PRIMARY KEY,
    order_id    NVARCHAR(20)  NOT NULL,
    old_status  NVARCHAR(20)  NOT NULL,
    new_status  NVARCHAR(20)  NOT NULL,
    changed_by  NVARCHAR(320) NOT NULL,
    request_id  NVARCHAR(40)  NOT NULL,
    changed_at  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);

-- Append-only. request_id is the primary key, so a request can never run twice.
CREATE TABLE dbo.agent_audit (
    request_id      NVARCHAR(40)  NOT NULL PRIMARY KEY,
    ticket_id       NVARCHAR(200) NOT NULL,
    runbook_id      NVARCHAR(100) NOT NULL,
    runbook_version INT           NOT NULL,
    runbook_hash    NVARCHAR(40)  NOT NULL,
    requester_email NVARCHAR(320) NOT NULL,
    order_id        NVARCHAR(20)  NOT NULL,
    old_status      NVARCHAR(20)  NOT NULL,
    new_status      NVARCHAR(20)  NOT NULL,
    outcome         NVARCHAR(40)  NOT NULL,
    created_at      DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);

INSERT INTO dbo.orders (order_id, customer_email, status) VALUES
    ('ORD-100001', 'alice@example.com', 'Pending'),
    ('ORD-100002', 'alice@example.com', 'Invoiced'),
    ('ORD-100003', 'bob@example.com',   'Pending'),
    ('ORD-100004', 'alice@example.com', 'Completed'),
    ('ORD-100005', 'bob@example.com',   'Invoiced');

-- The tool server's identity. Use the exact name of the Container App / Function App (its system-assigned managed identity).
CREATE USER [<managed-identity-name>] FROM EXTERNAL PROVIDER;

-- Exactly what the runbooks need, nothing more: no DELETE, no ALTER, no other tables, no schema access.
GRANT SELECT ON dbo.orders (order_id, customer_email, status, updated_at) TO [<managed-identity-name>];
GRANT UPDATE ON dbo.orders (status, updated_at)                           TO [<managed-identity-name>];
GRANT INSERT ON dbo.order_status_history                                  TO [<managed-identity-name>];
GRANT INSERT ON dbo.agent_audit                                           TO [<managed-identity-name>];

-- Verify: each of these must be DENIED. Run them after the grants.
EXECUTE AS USER = '<managed-identity-name>';
    SELECT USER_NAME() AS running_as;
    BEGIN TRY DELETE FROM dbo.orders WHERE 1 = 0;                          PRINT 'FAIL: DELETE allowed'; END TRY BEGIN CATCH PRINT 'ok: DELETE denied'; END CATCH
    BEGIN TRY UPDATE dbo.orders SET customer_email = customer_email WHERE 1 = 0; PRINT 'FAIL: other column update allowed'; END TRY BEGIN CATCH PRINT 'ok: other column denied'; END CATCH
    BEGIN TRY UPDATE dbo.agent_audit SET outcome = outcome WHERE 1 = 0;    PRINT 'FAIL: audit update allowed'; END TRY BEGIN CATCH PRINT 'ok: audit update denied'; END CATCH
    BEGIN TRY CREATE TABLE dbo.x (a INT);                                  PRINT 'FAIL: DDL allowed'; END TRY BEGIN CATCH PRINT 'ok: DDL denied'; END CATCH
REVERT;