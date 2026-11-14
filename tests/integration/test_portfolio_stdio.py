"""The published Portfolio server works over stdio exactly as an MCP host (Claude Desktop, Cursor) would run it."""
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]


async def test_stdio_host_can_list_and_search():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_servers.portfolio.server"], cwd=str(ROOT),
        env={**os.environ, "PORTFOLIO_DIR": str(ROOT / "data/personas/fullstack"), "MCP_TRANSPORT": "stdio",
             "RAG_EMBED_BACKEND": "hash"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        assert tools == {"search_evidence", "get_case_study", "list_skills"}
        templates = {t.uriTemplate for t in (await s.list_resource_templates()).resourceTemplates}
        assert {"portfolio://case/{id}", "portfolio://proposal/{id}"} <= templates
        assert len((await s.list_prompts()).prompts) == 3
        res = await s.call_tool("search_evidence", {"requirement": "subscription billing with Stripe", "k": 3})
        items = res.structuredContent["items"]
        assert items and all(i["type"] and i["id"].startswith("fullstack:") for i in items)
        skills = (await s.call_tool("list_skills", {})).structuredContent["skills"]
        assert any(x["skill"] == "Stripe" for x in skills)
