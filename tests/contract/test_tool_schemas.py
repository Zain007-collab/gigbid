"""Schema snapshot test (A7): fails CI when an MCP tool signature changes unintentionally.

Accept an intentional change with:  make snapshots
"""
import json
import os
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

SNAP_DIR = Path(__file__).parent / "snapshots"
SERVERS = {
    "marketplace": "mcp_servers.marketplace.server",
    # "portfolio": "mcp_servers.portfolio.server",
    # "workspace": "mcp_servers.workspace.server",
}

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("name", sorted(SERVERS))
async def test_tool_schema_snapshot(name):
    module = __import__(SERVERS[name], fromlist=["mcp"])
    async with create_connected_server_and_client_session(module.mcp._mcp_server) as client:
        tools = (await client.list_tools()).tools
    current = {
        t.name: {
            "inputSchema": t.inputSchema,
            "outputSchema": t.outputSchema,
            "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None,
        }
        for t in sorted(tools, key=lambda t: t.name)
    }
    path = SNAP_DIR / f"{name}.json"
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        SNAP_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.skip(f"created snapshot {path}; commit it")
    expected = json.loads(path.read_text())
    assert current == expected, f"{name} tool schemas changed — run `make snapshots` if intentional"
