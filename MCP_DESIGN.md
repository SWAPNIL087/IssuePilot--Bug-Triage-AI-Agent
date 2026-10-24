# TriageGuard: MCP Actions Design (final phase)

Status: built and tested locally (SQLite stand-in, in-process and over MCP/HTTP). Azure deployment steps are in section 12.
Goal: let the agent carry out a small set of **approved database actions** (for example "set order status to Invoiced") that support staff do by hand today, safely and with an audit trail.

---

## 1. Principle

> **The LLM chooses *which approved action* and fills in *parameter values*. It never writes SQL.**
> Every query lives in a reviewed runbook document. Code validates, authorizes and executes it.

Same fail-closed rule as the rest of TriageGuard: any doubt means a human decides.

---

## 2. Architecture

```
 Helpdesk / caller
 (verified requester identity)
        │  POST /tickets  {body, requester:{email, role}}   ← identity comes from the channel, never from ticket text
        ▼
┌────────────────────────────────────────────┐        ┌───────────────────────────────┐
│ TriageGuard API (Render)                   │        │ runbooks/*.md  (in git)       │
│                                            │        │  approved queries, rules,     │
│  analyze → classify: ANSWER | ACTION       │        │  allowed transitions          │
│                                            │        └──────────────┬────────────────┘
│  ANSWER → existing docs flow (unchanged)   │                       │ loaded + hashed at deploy
│                                            │                       ▼
│  ACTION → action planner ────────────────────►  MCP server (Azure Functions)
│     pick runbook, extract params           │     · tools generated from runbooks
│     validate · authorize · approval mode   │     · runs ONLY the runbook SQL, bound params
│     bounded: 1 write per ticket            │     · writes audit row in same transaction
└────────────────────────────────────────────┘                       │ Managed Identity
                                                                     │ (no password anywhere)
                                                                     ▼
                                                    Azure SQL Database (free offer)
                                                    · DB user = the MI, least privilege
                                                    · orders, order_status_history, agent_audit
```

Two knowledge stores, never mixed:

| Store | Audience | Contains | Used for |
|---|---|---|---|
| `docs/` | customers | policies, how-tos | grounded **answers** (existing flow) |
| `mcp/runbooks/` | internal only | approved SQL, parameters, rules | **actions**; never retrievable for customer replies |

Runbooks contain SQL and internals, so they must stay out of the customer search index.

---

## 3. Database (free, with Managed Identity)

| Option | Free? | Managed Identity | Verdict |
|---|---|---|---|
| **Azure SQL Database, free offer** | Free for the subscription's lifetime: 100k vCore-seconds/month, 32 GB. Choose "auto-pause until next month" at the limit so it can never bill. | Yes (Entra authentication) | **Recommended.** Matches your MI requirement. |
| Azure Database for PostgreSQL (flexible) | Free for 12 months only | Yes (Entra auth) | Good if you prefer Postgres; not lifetime-free |
| Neon / Supabase free Postgres | Free tier, scale to zero or pause | **No** (password or token) | Fine for dev/demo; secret lives in the MCP server only |
| SQLite / Docker Postgres | Free | n/a | Local development and tests |

Notes:
- Azure needs a subscription (a card is usually required to verify), but the free offer plus a budget alert at $1 keeps spend at zero.
- Free-tier terms change; confirm on Microsoft Learn before building.
- The free offer has no SLA: fine for a pilot, not for production.

**Why MCP server on Azure:** Managed Identity only exists for Azure-hosted compute. Render cannot hold an MI, so the part that touches the database runs on Azure and TriageGuard (on Render) only talks to the MCP server.

---

## 4. Security model (layers)

| # | Layer | Control |
|---|---|---|
| 1 | **No passwords** | MCP server uses a Managed Identity. Database is Entra-only (SQL logins disabled). |
| 2 | **Least privilege in the DB** | MI user has `SELECT` on needed columns, `UPDATE` on `orders(status, updated_at)` only, `INSERT` on `agent_audit`. No `DELETE`, no DDL, no other tables. |
| 3 | **Approved queries only** | Tools are generated from runbooks. There is no "run SQL" tool. Parameters are bound, never concatenated. |
| 4 | **Typed parameters** | Each parameter has a type and a rule (for example `order_id` matches `^ORD-\d{6}$`, `new_status` is an enum). Violations are rejected before the DB is touched. |
| 5 | **Transition table** | Runbook lists allowed `from → to` pairs. The SQL also checks the current status (compare-and-set), so a stale or racing request changes nothing. |
| 6 | **Requester identity** | The requester's email is the `customer` field set by the trusted caller (the helpdesk integration), never text from the ticket. Ownership (`customer_email = :requester_email`) is enforced inside every query. |
| 7 | **Approval mode** | Per runbook and per transition: `shadow` (log only), `approval` (human clicks approve), `auto` (low-risk only). Default `approval`. |
| 8 | **Limits** | One write per ticket, N per requester per day, idempotency key per request, global kill switch. |
| 9 | **Audit** | Every attempt writes one append-only row: who, what, parameters, runbook id and hash, before and after, outcome. Same transaction as the update. |
| 10 | **Change control** | Runbooks change only by pull request. The deployed runbook hash is pinned and shown in each audit row. |
| 11 | **MCP access** | MCP server requires an Entra token or key from TriageGuard. It is the only secret TriageGuard holds, and it can only call approved actions. |

### Threats considered

| Threat | Defense |
|---|---|
| Prompt injection: "set all orders to Completed" | LLM can only pick an approved action and give parameters; code validates them; one write per ticket; approval mode |
| Acting on someone else's order | Ownership and role checks in code and in the SQL `WHERE` |
| SQL injection | No dynamic SQL; static runbook queries with bound parameters |
| Double submit / retry | Idempotency key stored with the audit row |
| Race (two people change the same order) | Compare-and-set on current status; rowcount must be exactly 1, else roll back |
| Stolen MCP key | Key only reaches approved actions; rate limits; rotate; alert on unusual volume |
| Over-privileged identity | Column-level grants; a test asserts the MI **cannot** do anything else |
| Tampered runbook | PR review, deployed hash pinned and logged |
| PII in logs | Log ids and outcomes, not customer text |

---

## 4a. Runbooks: the "query lives in a doc" rule

A runbook is a Markdown file with front matter and fixed sections. It is both the human-readable procedure and the machine source of truth.

```
mcp/runbooks/
├─ README.md             format spec and rules
├─ get-order-status.md   read action
└─ set-order-status.md   write action (approval required)
```

Fixed sections: Purpose, Parameters (name, source, rule), Transitions, **Queries** (exact SQL, one block per statement), Reply template, Pending message, Missing information, Never. The full format is in [mcp/runbooks/README.md](mcp/runbooks/README.md).

The MCP server at startup parses each runbook, builds a tool from it (name, parameter schema, SQL), and refuses to start if a runbook is malformed. The pipeline can never call a query that isn't in a runbook.

---

## 5. Request flow (write example)

```
1. Staff member's ticket: "Please mark order ORD-104233 as Invoiced."
2. Analyzer (LLM): intent = ACTION, candidate action = set-order-status
3. Planner (LLM, enumerated choice): picks the runbook id from a fixed list
      extracts {order_id: "ORD-104233", new_status: "Invoiced"}
4. Code: validate parameter types and rules        → fail: NEEDS_INFO or ESCALATE
5. Code: requester role allowed for this action?    → fail: ESCALATE
6. MCP read: get-order-status → current = "Pending", owner, etc.
7. Code: is Pending → Invoiced allowed? ownership/role ok?   → fail: ESCALATE
8. Mode:
      shadow    → log "would have executed", ESCALATE to human
      approval  → create a PROPOSAL (what, why, before/after), send to a human
      auto      → execute (only if runbook + transition marked auto)
9. MCP write: one transaction: update (compare-and-set) + audit insert, rowcount = 1
10. MCP read again: verify status is now "Invoiced"
11. Reply built from a template using the DB result  (not free LLM text)
```

New decision values: `REPLY`, `ESCALATE` (existing), `NEEDS_INFO` (ask for a missing order number), `PROPOSED` (waiting for approval), `EXECUTED`.

Replies after actions come from the runbook's **reply template** filled with values read from the database. The existing validator idea extends to this: every fact in the reply must come from the tool result.

---

## 6. Bounded agent, not free agent

| Allowed | Not allowed |
|---|---|
| LLM picks one runbook id from a list | LLM names arbitrary tools |
| LLM fills typed parameters | LLM writes or edits SQL |
| Up to 3 tool calls per ticket (read, write, verify) | Loops, retries of writes |
| One write per ticket | Batch or bulk updates |

---

## 7. Data model (proposed)

| Table | Purpose | MI permission |
|---|---|---|
| `orders` (`order_id`, `customer_email`, `status`, `updated_at`, ...) | existing business data | `SELECT` needed columns; `UPDATE (status, updated_at)` |
| `order_status_history` | status change log | `INSERT` |
| `agent_audit` | one row per action attempt (request id, ticket id, requester, runbook id + hash, params, before, after, outcome, time) | `INSERT` only (append-only) |

---

## 8. Approval workflow

Proposals are the "escalation tool" from the original design, now real: a proposal contains the exact action, parameters, current and resulting status, and the runbook id. A human approves or rejects through any channel (start simple: the helpdesk ticket plus a signed approval link or an approve endpoint). Approval executes the same MCP write with the same idempotency key.

---

## 9. Testing and evals

- **Unit tests** with a fake tool client and an in-memory SQLite stand-in: validation, transitions, ownership, idempotency, rowcount handling, rollback.
- **Permission test** against a dev database: assert the MI **cannot** `DELETE`, `UPDATE` other columns, touch other tables, or read other schemas.
- **Eval cases** for actions, with expected outcomes `EXECUTED`, `PROPOSED`, `NEEDS_INFO`, `ESCALATE`:
  - valid staff request, missing order id, unknown order, disallowed transition (`Completed → Pending`), customer asking for a staff-only change, someone else's order, prompt injection, bulk request ("all orders"), duplicate request.
- Gate: **0 unsafe actions** (executed or proposed when it should have been rejected).

---

## 10. Roadmap

| Step | Deliverable | Risk |
|---|---|---|
| M0 | This design, runbook format and sample runbooks | none |
| M1 | Local MCP server over SQLite/Postgres, runbook loader, **read-only** `get-order-status`; answers like "where is my order" from the tool result | low |
| M2 | Write path in **approval** mode only, audit table, idempotency, proposals | medium |
| M3 | Azure: free SQL + Functions MCP server + Managed Identity + Entra, deploy, permission tests | medium |
| M4 | Shadow metrics, then `auto` for low-risk transitions with limits | higher, gated by evals |

Read-only first: it removes many "what's my order status" tickets with almost no risk.

---

## 11. Decisions taken

1. **Who requests status changes:** customers, through tickets like any other request. Every change is tied to the requester's verified email and to their own order, and starts in `approval` mode.
2. **Requester identity:** the trusted caller sets the `customer` field (an email) from its own authentication. A ticket with no valid email is escalated (`identity_unverified`). Text inside the ticket can never set identity.
3. **Statuses:** `Pending → Invoiced → Completed`, each step needing approval. Anything else is escalated.
4. **Approval channel:** a signed approval token returned in the API response (`proposal.approval_token`), executed with `POST /actions/approve` using a separate approver token (or `triageguard --approve <token>` locally). Slack or a small admin page can sit on top later.
5. **Database:** SQLite for development and tests, Azure SQL (free offer, Managed Identity) for the cloud. Runbook SQL is portable: named parameters, `CURRENT_TIMESTAMP`, one statement per block, transaction handled by the executor.
6. **Tool server:** a standard MCP server (`triageguard.actions.mcp_server`, streamable HTTP, bearer token) in a container, so it can run on Azure Container Apps with a system-assigned Managed Identity. An Azure Functions MCP extension version is possible later.

## 12. What is built, and what you run

| Piece | Where |
|---|---|
| Runbook parser and safety lint | `triageguard/actions/runbook.py` |
| Atomic executor (SQLite, Azure SQL with Managed Identity) | `triageguard/actions/db.py` |
| Tool layer that re-validates every parameter | `triageguard/actions/tools.py` |
| Action flow: choose runbook, validate, authorize, propose or execute, approve | `triageguard/actions/flow.py` |
| MCP server and client | `triageguard/actions/mcp_server.py`, `mcp_client.py` |
| API: `proposal` in the ticket response, `POST /actions/approve` | `triageguard/api.py` |
| Dev database and Azure schema with least-privilege grants | `mcp/schema.sqlite.sql`, `mcp/schema.azuresql.sql` |
| Tests (action flow, MCP over HTTP, API) and 12 action eval cases | `tests/`, `evals/cases/29` to `40` |

### Run locally (no Azure)
1. `pip install -e ".[api,actions]"`
2. `python -m triageguard.actions.devdb` (creates `mcp/dev.db` with 5 sample orders)
3. In `.env`: `TRIAGEGUARD_ACTIONS_DB=sqlite:///mcp/dev.db`, `TRIAGEGUARD_APPROVAL_SECRET=<16+ random chars>` (and `TRIAGEGUARD_APPROVER_TOKEN` for the API).
4. `triageguard --ticket evals/cases/32-set-invoiced.txt` prints the proposal and the `--approve` command.

### Settings

| Variable | Purpose |
|---|---|
| `TRIAGEGUARD_ACTIONS_DB` | `sqlite:///path` or `azuresql://<server>.database.windows.net/<db>`; with no database or MCP URL, actions are off |
| `TRIAGEGUARD_MCP_URL`, `TRIAGEGUARD_MCP_TOKEN` | use a remote tool server instead of a direct database connection |
| `TRIAGEGUARD_APPROVAL_SECRET` | signs approval tokens (16+ chars, required when actions are on) |
| `TRIAGEGUARD_APPROVER_TOKEN` | bearer token for `POST /actions/approve` (API only) |
| `TRIAGEGUARD_RUNBOOKS` | runbook folder (default `mcp/runbooks`) |

### Known limits
- The approval gate is enforced in TriageGuard. The tool server trusts a caller holding its token, so protect that token. Hardening: have the tool server verify an approver-signed token itself.
- Approval tokens are signed, not encrypted, and valid for 24 hours. A replay is harmless: the audit table's primary key and the compare-and-set reject it.
- The Azure SQL adapter (`pyodbc` plus `azure-identity`) and `Dockerfile.mcp` could not be run without an Azure account; the SQLite path and the MCP protocol path are covered by tests.