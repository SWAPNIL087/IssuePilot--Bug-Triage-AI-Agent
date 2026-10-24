import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

try:
    import uvicorn
    from triageguard.actions import mcp_server
    from triageguard.actions.mcp_client import McpTools
except ImportError:
    raise unittest.SkipTest("install the actions extra: pip install -e .[api,actions]")

from test_core import CFG, KB
from triageguard.actions import devdb
from triageguard.actions.flow import Actions, approve
from triageguard.actions.runbook import ActionError, load_runbooks
from triageguard.config import ROOT
from triageguard.intake.loader import Ticket
from triageguard.pipeline import Decision, process

TOKEN = "m" * 20


class TestMcpServerOverHttp(unittest.TestCase):
    """A real MCP server on a local port, called through the real MCP client."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name, "dev.db")
        devdb.create(cls.db)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {"TRIAGEGUARD_ACTIONS_DB": f"sqlite:///{cls.db.as_posix()}", "TRIAGEGUARD_MCP_TOKEN": TOKEN}
        with mock.patch.dict(os.environ, env):
            app = mcp_server.create_app()
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 15
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.tmp.cleanup()

    def status(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT status FROM orders WHERE order_id = 'ORD-100001'").fetchone()[0]

    def test_read_through_the_tool_server(self):
        rows = McpTools(self.url, TOKEN).call(
            "get-order-status", {"order_id": "ORD-100001", "requester_email": "alice@example.com"})["rows"]
        self.assertEqual(rows[0]["order_id"], "ORD-100001")

    def test_server_rejects_bad_token_and_unknown_tools_and_foreign_orders(self):
        with self.assertRaises(ActionError):
            McpTools(self.url, "x" * 20).call("get-order-status", {"order_id": "ORD-100001", "requester_email": "a@b.co"})
        with self.assertRaises(ActionError):
            McpTools(self.url, TOKEN).call("run-sql", {"sql": "DROP TABLE orders"})
        with self.assertRaisesRegex(ActionError, "matched 0 rows"):
            McpTools(self.url, TOKEN).call(
                "get-order-status", {"order_id": "ORD-100003", "requester_email": "alice@example.com"})
        with self.assertRaisesRegex(ActionError, "invalid"):
            McpTools(self.url, TOKEN).call(
                "get-order-status", {"order_id": "ORD-1' OR '1'='1", "requester_email": "alice@example.com"})

    def test_full_flow_pipeline_to_database_through_mcp(self):
        runbooks = load_runbooks(ROOT / "mcp" / "runbooks")
        actions = Actions(runbooks, McpTools(self.url, TOKEN), "s" * 20)
        analysis = {"intent": "x", "topic": "x", "sensitive": False, "wants_human": False, "search_query": "order",
                    "action": "set-order-status", "action_params": {"order_id": "ORD-100001", "new_status": "Invoiced"}}
        ticket = Ticket("mcp-1", "mark it invoiced", customer="alice@example.com")
        d = process(ticket, KB, lambda system, user: analysis, CFG, actions=actions)
        self.assertEqual(d.decision, "PROPOSED")
        self.assertEqual(self.status(), "Pending")
        done = approve(Decision("x"), d.action["approval_token"], actions)
        self.assertEqual(done.decision, "EXECUTED")
        self.assertEqual(self.status(), "Invoiced")


if __name__ == "__main__":
    unittest.main()
