import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("install the api extra: pip install -e .[api]")

from test_core import fake_llm
from triageguard import api
from triageguard.actions import devdb

TOKEN = "t" * 20


class TestAPI(unittest.TestCase):
    def setUp(self):
        os.environ["TRIAGEGUARD_API_TOKEN"] = TOKEN
        os.environ.setdefault("OPENAI_API_KEY", "test-key")
        os.environ["TRIAGEGUARD_EMBED_MODEL"] = ""  # no network in tests
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = TestClient(api.app)
        self.client = self.ctx.__enter__()
        cfg = api.app.state.cfg
        api.app.state.cfg = replace(cfg, output=Path(self.tmp.name, "out"), log=Path(self.tmp.name, "log.jsonl"))
        api.app.state.llm = fake_llm()  # replace the real LLM
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_health_is_open(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_requires_token(self):
        body = {"body": "How do I reset my password?"}
        self.assertEqual(self.client.post("/tickets", json=body).status_code, 401)
        self.assertEqual(self.client.post("/tickets", json=body, headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_reply_and_validation(self):
        r = self.client.post("/tickets", json={"id": "api-1", "body": "How do I reset my password?"}, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["decision"], "REPLY")
        self.assertIn("Account Access › Resetting your password", r.json()["sources"])
        self.assertTrue(r.json()["customer_message"].startswith("Use the reset link"))
        self.assertEqual(self.client.post("/tickets", json={"body": "  "}, headers=self.auth).status_code, 422)
        self.assertEqual(os.listdir(self.tmp.name), [])  # nothing is written to disk

    def test_escalation(self):
        api.app.state.llm = fake_llm(analysis={"wants_human": True})
        r = self.client.post("/tickets", json={"body": "give me a human"}, headers=self.auth)
        self.assertEqual(r.json()["decision"], "ESCALATE")
        self.assertEqual(r.json()["reason_code"], "customer_requested_human")
        self.assertIn("passed your request to our support team", r.json()["customer_message"])
        self.assertEqual(os.listdir(self.tmp.name), [])  # nothing is written to disk


class TestAPIActions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name, "dev.db")
        devdb.create(db_path)
        self.db_path = db_path
        self.env = mock.patch.dict(os.environ, {
            "TRIAGEGUARD_API_TOKEN": TOKEN, "TRIAGEGUARD_APPROVER_TOKEN": "a" * 20,
            "TRIAGEGUARD_APPROVAL_SECRET": "s" * 20, "TRIAGEGUARD_EMBED_MODEL": "",
            "OPENAI_API_KEY": "test-key", "TRIAGEGUARD_ACTIONS_DB": f"sqlite:///{db_path.as_posix()}"})
        self.env.start()
        self.ctx = TestClient(api.app)
        self.client = self.ctx.__enter__()
        self.analysis = {"intent": "x", "topic": "x", "sensitive": False, "wants_human": False,
                         "search_query": "order", "action": "set-order-status",
                         "action_params": {"order_id": "ORD-100001", "new_status": "Invoiced"}}
        api.app.state.llm = lambda system, user: self.analysis
        self.ticket_auth = {"Authorization": f"Bearer {TOKEN}"}
        self.approver_auth = {"Authorization": "Bearer " + "a" * 20}

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def status(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute("SELECT status FROM orders WHERE order_id = 'ORD-100001'").fetchone()[0]

    def test_propose_then_approve(self):
        r = self.client.post("/tickets", json={"id": "a1", "customer": "alice@example.com", "body": "set it"},
                             headers=self.ticket_auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["decision"], "PROPOSED")
        token = r.json()["proposal"]["approval_token"]
        self.assertEqual(self.status(), "Pending")

        body = {"token": token}
        self.assertEqual(self.client.post("/actions/approve", json=body).status_code, 401)
        self.assertEqual(self.client.post("/actions/approve", json=body, headers=self.ticket_auth).status_code, 401)
        ok = self.client.post("/actions/approve", json=body, headers=self.approver_auth)
        self.assertEqual((ok.status_code, ok.json()["decision"]), (200, "EXECUTED"))
        self.assertEqual(self.status(), "Invoiced")
        self.assertEqual(self.client.post("/actions/approve", json=body, headers=self.approver_auth).status_code, 409)

    def test_ticket_without_verified_customer_is_escalated(self):
        r = self.client.post("/tickets", json={"id": "a2", "body": "set it"}, headers=self.ticket_auth)
        self.assertEqual((r.json()["decision"], r.json()["reason_code"]), ("ESCALATE", "identity_unverified"))
        self.assertEqual(self.status(), "Pending")

    def test_refuses_to_start_without_approver_token(self):
        with mock.patch.dict(os.environ, {"TRIAGEGUARD_APPROVER_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                with TestClient(api.app):
                    pass

if __name__ == "__main__":
    unittest.main()
