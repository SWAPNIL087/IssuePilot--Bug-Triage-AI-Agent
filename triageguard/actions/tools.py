"""Tool layer: runs approved runbooks. The MCP server and the in-process path both use `Tools`."""
import os
from pathlib import Path

from triageguard.actions import db
from triageguard.actions.flow import Actions
from triageguard.actions.runbook import ActionError, Runbook, load_runbooks
from triageguard.config import Config


class Tools:
    """Executes a runbook by id. Re-validates every parameter, so a caller cannot bypass the rules."""

    def __init__(self, runbooks: dict[str, Runbook], db_url: str):
        self.runbooks, self.db_url = runbooks, db_url

    def call(self, runbook_id: str, params: dict) -> dict:
        book = self.runbooks.get(runbook_id)
        if book is None:
            raise ActionError(f"unknown runbook {runbook_id!r}")
        values = {}
        for p in book.params:
            if not p.check(params.get(p.name)):
                raise ActionError(f"parameter '{p.name}' is missing or invalid")
            values[p.name] = params[p.name]
        if "runbook_hash" in values and values["runbook_hash"] != book.hash:
            raise ActionError("runbook version mismatch between caller and tool server")
        return {"rows": db.run(self.db_url, book, values), "runbook_hash": book.hash}


def local_tools(runbooks_dir: Path, db_url: str) -> tuple[dict[str, Runbook], Tools]:
    runbooks = load_runbooks(runbooks_dir)
    return runbooks, Tools(runbooks, db_url)


def build_actions(cfg: Config) -> Actions | None:
    """None when actions are not configured (the agent then only answers from the docs)."""
    if not (cfg.actions_db or cfg.mcp_url):
        return None
    secret = os.environ.get("TRIAGEGUARD_APPROVAL_SECRET", "")
    if len(secret) < 16:  # fail closed: approval tokens must be signed with a real secret
        raise ActionError("Set TRIAGEGUARD_APPROVAL_SECRET (16+ characters) to enable actions")
    if cfg.mcp_url:  # the database is reached through the remote MCP server, never directly
        from triageguard.actions.mcp_client import McpTools

        token = os.environ.get("TRIAGEGUARD_MCP_TOKEN", "")
        if len(token) < 16:
            raise ActionError("Set TRIAGEGUARD_MCP_TOKEN (16+ characters) to use the MCP tool server")
        return Actions(load_runbooks(cfg.runbooks), McpTools(cfg.mcp_url, token), secret)
    runbooks, tools = local_tools(cfg.runbooks, cfg.actions_db)
    return Actions(runbooks, tools, secret)