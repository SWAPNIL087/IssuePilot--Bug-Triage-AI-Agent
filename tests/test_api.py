import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("install the api extra: pip install -e .[api]")

from test_core import fake_llm
from triageguard import api

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


if __name__ == "__main__":
    unittest.main()
