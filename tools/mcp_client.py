"""Call workshop tools through a real MCP stdio subprocess."""

import asyncio
import json
import os
from pathlib import Path
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def _execute(tool_name: str, arguments: dict):
    """Start the server, initialize MCP, call one tool, and close the session."""
    names = ("PATH", "HOME", "TMPDIR", "SYSTEMROOT", "WINDIR",
             "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_TOKEN",
             "GITHUB_API_URL", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    # Tools need GitHub access on Actions, but not the model API key.
    environment = {name: os.environ[name] for name in names if name in os.environ}
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "tools.mcp_server"],
        cwd=str(ROOT), env=environment,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                tool_name, arguments, read_timeout_seconds=45,
            )
            text = "\n".join(item.text for item in result.content
                             if item.type == "text")
            failed = result.is_error
            value = result.structured_content
            if isinstance(value, dict) and set(value) == {"result"}:
                value = value["result"]
            if value is None:
                value = text
    # Raise after shutdown so a server error stays readable to the caller.
    if failed:
        raise RuntimeError(f"MCP tool {tool_name} failed: {text}")
    if tool_name == "run_test_suite" and isinstance(value, str):
        value = json.loads(value)
    return value


def execute_mcp_tool(tool_name: str, arguments: dict = None):
    """Keep the existing synchronous interface used by both agent flows."""
    return asyncio.run(_execute(tool_name, arguments or {}))
