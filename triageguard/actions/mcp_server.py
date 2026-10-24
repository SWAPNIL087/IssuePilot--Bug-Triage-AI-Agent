"""MCP server: one tool per approved runbook, nothing else. There is no generic "run SQL" tool.

  stdio (local MCP clients):   python -m triageguard.actions.mcp_server
  HTTP  (remote, behind a bearer token):
                               uvicorn --factory triageguard.actions.mcp_server:create_app --port 8001
Settings come from the environment: TRIAGEGUARD_ACTIONS_DB, TRIAGEGUARD_RUNBOOKS, TRIAGEGUARD_MCP_TOKEN.
"""
import hmac
import inspect
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from triageguard.actions.runbook import ActionError, Runbook
from triageguard.actions.tools import Tools, local_tools
from triageguard.config import Config, load_env


def _tool(book: Runbook, tools: Tools):
    def run(**params):
        try:
            return tools.call(book.id, params)
        except ActionError as e:
            raise ToolError(str(e)) from e  # shown to the caller as an MCP error result

    run.__signature__ = inspect.Signature([
        inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY, annotation=int if p.name == "runbook_version" else str)
        for p in book.params
    ])
    run.__name__ = book.id.replace("-", "_")
    return run


def build_server(tools: Tools) -> MCPServer:
    server = MCPServer("triageguard-actions", instructions="Approved database actions. Each tool runs one reviewed runbook.")
    for book in tools.runbooks.values():
        server.add_tool(_tool(book, tools), name=book.id,
                        description=f"{book.purpose} (risk: {book.risk}, runbook v{book.version})")
    return server


class BearerAuth:
    """ASGI wrapper: every HTTP request must carry the shared bearer token."""

    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def _tools_from_env() -> Tools:
    load_env()
    cfg = Config.from_env()
    if not cfg.actions_db:
        raise RuntimeError("Set TRIAGEGUARD_ACTIONS_DB")
    return local_tools(cfg.runbooks, cfg.actions_db)[1]


def create_app():
    token = os.environ.get("TRIAGEGUARD_MCP_TOKEN", "")
    load_env()
    token = token or os.environ.get("TRIAGEGUARD_MCP_TOKEN", "")
    if len(token) < 16:  # fail closed: never serve an open endpoint
        raise RuntimeError("Set TRIAGEGUARD_MCP_TOKEN to a secret of at least 16 characters")
    # host=0.0.0.0 turns off the localhost-only Host check, which would reject the public hostname; the bearer token protects it
    return BearerAuth(build_server(_tools_from_env()).streamable_http_app(host="0.0.0.0"), token)


if __name__ == "__main__":
    build_server(_tools_from_env()).run()  # stdio
