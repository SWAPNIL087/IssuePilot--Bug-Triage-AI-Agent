"""MCP client: calls the remote tool server. Same `.call(runbook_id, params)` interface as the in-process Tools."""
import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from triageguard.actions.runbook import ActionError


class McpTools:
    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self.url, self.token, self.timeout = url, token, timeout

    def call(self, runbook_id: str, params: dict) -> dict:
        try:
            return asyncio.run(self._call(runbook_id, params))
        except ActionError:
            raise
        except Exception as e:  # network, auth or protocol failure: the caller escalates
            raise ActionError(f"tool server error: {e}") from e

    async def _call(self, runbook_id: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx2.AsyncClient(headers=headers, timeout=self.timeout) as http:
            async with Client(streamable_http_client(self.url, http_client=http)) as client:
                result = await client.call_tool(runbook_id, params)
        text = "".join(getattr(part, "text", "") for part in result.content)
        if result.is_error:
            raise ActionError(text or "tool error")
        return json.loads(text)
