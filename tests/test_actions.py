import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_core import CFG, KB
from triageguard.actions import devdb, flow
from triageguard.actions.flow import Actions, approve
from triageguard.actions.runbook import ActionError, RunbookError, load_runbooks, parse_runbook
from triageguard.actions.tools import Tools
from triageguard.config import ROOT
from triageguard.intake.loader import Ticket
from triageguard.pipeline import Decision, process

RUNBOOKS = ROOT / "mcp" / "runbooks"
SECRET = "s" * 20


def analysis_llm(action="", params=None, **extra):
    """Stand-in LLM for the action path: the analyzer is the only call."""
    out = {"intent": "x", "topic": "x", "sensitive": False, "wants_human": False, "search_query": "order",
           "action": action, "action_params": params or {}, **extra}
    return lambda system, user: out


class ActionsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name, "dev.db")
        devdb.create(self.db_path)
        self.url = f"sqlite:///{self.db_path.as_posix()}"
        self.actions = self.make_actions(RUNBOOKS)

    def tearDown(self):
        self.tmp.cleanup()

    def make_actions(self, directory):
        runbooks = load_runbooks(directory)
        return Actions(runbooks, Tools(runbooks, self.url), SECRET)

    def run_ticket(self, action, params, customer="alice@example.com", actions=None, ticket_id="t1"):
        ticket = Ticket(ticket_id, "please help with my order", customer=customer)
        return process(ticket, KB, analysis_llm(action, params), CFG, actions=actions or self.actions)

    def row(self, sql, *args):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(sql, args).fetchall()

    def change(self, sql, *args):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(sql, args)
            conn.commit()

    def status(self, order_id):
        return self.row("SELECT status FROM orders WHERE order_id = ?", order_id)[0][0]

    def modes_changed(self, mode):
        """A copy of the runbooks with every mode switched, for shadow/auto tests."""
        copy = Path(self.tmp.name, f"runbooks-{mode}")
        shutil.copytree(RUNBOOKS, copy)
        for f in copy.glob("*.md"):
            f.write_text(f.read_text(encoding="utf-8").replace("mode: approval", f"mode: {mode}")
                         .replace("| approval |", f"| {mode} |"), encoding="utf-8")
        return self.make_actions(copy)


class TestRunbooks(ActionsCase):
    def test_loads_both_runbooks(self):
        self.assertEqual(set(self.actions.runbooks), {"get-order-status", "set-order-status"})
        book = self.actions.runbooks["set-order-status"]
        self.assertEqual((book.risk, book.mode, book.precheck), ("write", "approval", "get-order-status"))
        self.assertEqual(book.transition_mode("Pending", "Invoiced"), "approval")
        self.assertIsNone(book.transition_mode("Completed", "Pending"))

    def test_rejects_unsafe_or_malformed_runbooks(self):
        base = (RUNBOOKS / "get-order-status.md").read_text(encoding="utf-8")
        bad = {
            "delete": base.replace("SELECT order_id, status, updated_at", "DELETE FROM orders; SELECT 1"),
            "drop": base.replace("SELECT order_id, status, updated_at\nFROM   orders", "DROP TABLE orders"),
            "undeclared parameter": base.replace(":requester_email", ":someone_else"),
            "missing section": base.replace("## Reply template", "## Something else"),
            "no id": base.replace("id: get-order-status\n", ""),
        }
        for name, text in bad.items():
            path = Path(self.tmp.name, "bad.md")
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(RunbookError, msg=name):
                parse_runbook(path)

    def test_update_without_where_and_write_without_audit_are_rejected(self):
        base = (RUNBOOKS / "set-order-status.md").read_text(encoding="utf-8")
        for name, text in {
            "update without where": base.replace("WHERE  order_id = :order_id\n  AND  status = :expected_current_status\n  AND  customer_email = :requester_email\n```\n\n```sql name=history", "\n```\n\n```sql name=history"),
            "no audit": base.replace("name=audit", "name=other"),
            "row count unchecked": base.replace("name=update expect_rows=1", "name=update"),
        }.items():
            path = Path(self.tmp.name, "bad.md")
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(RunbookError, msg=name):
                parse_runbook(path)


class TestReadAction(ActionsCase):
    def test_owner_gets_status(self):
        d = self.run_ticket("get-order-status", {"order_id": "ORD-100001"})
        self.assertEqual(d.decision, "REPLY")
        self.assertIn('"Pending"', d.answer)

    def test_other_customers_order_is_not_revealed(self):
        d = self.run_ticket("get-order-status", {"order_id": "ORD-100003"})  # belongs to bob
        self.assertEqual((d.decision, d.reason_code), ("ESCALATE", "action_failed"))
        self.assertNotIn("Pending", d.answer)

    def test_missing_or_malformed_order_number_asks_for_it(self):
        for params in ({}, {"order_id": "ORD-12"}, {"order_id": "all orders"}):
            d = self.run_ticket("get-order-status", params)
            self.assertEqual(d.decision, "NEEDS_INFO", params)
            self.assertIn("ORD-123456", d.answer)

    def test_unverified_requester_is_escalated(self):
        d = self.run_ticket("get-order-status", {"order_id": "ORD-100001"}, customer="")
        self.assertEqual(d.reason_code, "identity_unverified")

    def test_unknown_runbook_is_escalated(self):
        self.assertEqual(self.run_ticket("drop-all-tables", {}).reason_code, "unknown_action")


class TestWriteAction(ActionsCase):
    request = {"order_id": "ORD-100001", "new_status": "Invoiced"}

    def propose(self, **kw):
        return self.run_ticket("set-order-status", self.request, **kw)

    def test_proposal_changes_nothing_until_approved(self):
        d = self.propose()
        self.assertEqual((d.decision, d.reason_code), ("PROPOSED", "awaiting_approval"))
        self.assertIn("Our team will confirm", d.answer)
        self.assertEqual(self.status("ORD-100001"), "Pending")
        self.assertEqual(self.row("SELECT COUNT(*) FROM agent_audit")[0][0], 0)

    def test_approval_executes_once_with_audit_trail(self):
        token = self.propose().action["approval_token"]
        done = approve(Decision("x"), token, self.actions)
        self.assertEqual(done.decision, "EXECUTED")
        self.assertIn('"Invoiced"', done.answer)
        self.assertEqual(self.status("ORD-100001"), "Invoiced")
        audit = self.row("SELECT ticket_id, runbook_id, runbook_hash, requester_email, old_status, new_status, outcome FROM agent_audit")
        self.assertEqual(audit, [("t1", "set-order-status", self.actions.runbooks["set-order-status"].hash,
                                  "alice@example.com", "Pending", "Invoiced", "executed")])
        self.assertEqual(self.row("SELECT COUNT(*) FROM order_status_history")[0][0], 1)
        again = approve(Decision("x"), token, self.actions)  # replay
        self.assertEqual((again.decision, again.reason_code), ("ESCALATE", "action_failed"))
        self.assertEqual(self.row("SELECT COUNT(*) FROM agent_audit")[0][0], 1)

    def test_stale_proposal_is_rejected_if_the_order_changed(self):
        token = self.propose().action["approval_token"]
        self.change("UPDATE orders SET status = 'Completed' WHERE order_id = 'ORD-100001'")
        self.assertEqual(approve(Decision("x"), token, self.actions).reason_code, "action_failed")
        self.assertEqual(self.status("ORD-100001"), "Completed")

    def test_bad_tokens_are_rejected(self):
        token = self.propose().action["approval_token"]
        body, sig = token.split(".")
        for bad in (body + ".AAAA", "junk", body[:-2] + "xx." + sig):
            self.assertEqual(approve(Decision("x"), bad, self.actions).reason_code, "invalid_approval")
        flow.APPROVAL_TTL_S, saved = -10, flow.APPROVAL_TTL_S
        try:
            expired = self.propose().action["approval_token"]
        finally:
            flow.APPROVAL_TTL_S = saved
        self.assertIn("expired", approve(Decision("x"), expired, self.actions).reason)

    def test_other_customers_order_cannot_be_changed(self):
        d = self.run_ticket("set-order-status", {"order_id": "ORD-100003", "new_status": "Invoiced"})
        self.assertEqual((d.decision, d.reason_code), ("ESCALATE", "action_failed"))
        self.assertEqual(self.status("ORD-100003"), "Pending")

    def test_only_approved_transitions(self):
        for order, new in (("ORD-100001", "Completed"),   # skips a step
                           ("ORD-100004", "Invoiced"),     # out of Completed
                           ("ORD-100002", "Invoiced")):    # no change
            d = self.run_ticket("set-order-status", {"order_id": order, "new_status": new})
            self.assertEqual(d.reason_code, "transition_not_allowed", (order, new))

    def test_status_outside_the_allowed_list_is_escalated(self):
        d = self.run_ticket("set-order-status", {"order_id": "ORD-100001", "new_status": "Shipped"})
        self.assertEqual(d.reason_code, "action_not_allowed")
        d = self.run_ticket("set-order-status", {"order_id": "ORD-100001", "new_status": {"x": 1}})
        self.assertEqual(d.reason_code, "action_not_allowed")

    def test_shadow_mode_logs_but_never_acts(self):
        d = self.run_ticket("set-order-status", self.request, actions=self.modes_changed("shadow"))
        self.assertEqual((d.decision, d.reason_code), ("ESCALATE", "action_shadow"))
        self.assertEqual(self.status("ORD-100001"), "Pending")

    def test_auto_mode_executes_and_verifies(self):
        d = self.run_ticket("set-order-status", self.request, actions=self.modes_changed("auto"))
        self.assertEqual(d.decision, "EXECUTED")
        self.assertEqual(self.status("ORD-100001"), "Invoiced")


class TestToolsAreEnforcedServerSide(ActionsCase):
    def params(self, **over):
        book = self.actions.runbooks["set-order-status"]
        base = {"order_id": "ORD-100001", "new_status": "Invoiced", "requester_email": "alice@example.com",
                "expected_current_status": "Pending", "request_id": "r1", "ticket_id": "t1",
                "runbook_id": book.id, "runbook_version": book.version, "runbook_hash": book.hash}
        return {**base, **over}

    def test_tool_revalidates_every_parameter(self):
        for over in ({"new_status": "Hacked"}, {"order_id": "ORD-1; DROP TABLE orders"}, {"runbook_hash": "stale"}):
            with self.assertRaises(ActionError, msg=over):
                self.actions.tools.call("set-order-status", self.params(**over))
        self.assertEqual(self.status("ORD-100001"), "Pending")

    def test_failed_statement_rolls_back_everything(self):
        self.actions.tools.call("set-order-status", self.params())
        # same request_id again: the audit insert fails, so the status change must roll back too
        self.change("UPDATE orders SET status = 'Pending' WHERE order_id = 'ORD-100001'")
        with self.assertRaises(ActionError):
            self.actions.tools.call("set-order-status", self.params())
        self.assertEqual(self.status("ORD-100001"), "Pending")

    def test_unknown_runbook_has_no_tool(self):
        with self.assertRaises(ActionError):
            self.actions.tools.call("run-sql", {"sql": "DELETE FROM orders"})


if __name__ == "__main__":
    unittest.main()
