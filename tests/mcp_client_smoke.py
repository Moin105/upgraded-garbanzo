"""Manual smoke test: drive the karst MCP server over stdio like a real host.

Run: python tests/mcp_client_smoke.py
Not part of the pytest suite (it launches a subprocess + loads the embedder).
"""

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = "C:/Users/hp/Byfoods"


async def main() -> None:
    params = StdioServerParameters(
        command="python", args=["-m", "karst.mcp_server"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            print("=" * 60)

            r = await session.call_tool("index_status", {"repo_path": REPO})
            print("index_status:\n" + r.content[0].text)
            print("=" * 60)

            r = await session.call_tool("list_packs", {"repo_path": REPO})
            print("list_packs:\n" + r.content[0].text[:400])
            print("=" * 60)

            r = await session.call_tool(
                "search_code",
                {"query": "how does the auth controller handle login", "repo_path": REPO, "limit": 3},
            )
            print("search_code:\n" + r.content[0].text[:900])
            print("=" * 60)

            r = await session.call_tool(
                "find_impact", {"symbol": "login", "repo_path": REPO, "max_depth": 2}
            )
            print("find_impact:\n" + r.content[0].text[:600])


if __name__ == "__main__":
    asyncio.run(main())
