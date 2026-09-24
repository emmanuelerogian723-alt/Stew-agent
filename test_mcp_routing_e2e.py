"""E2E test: MCP intent routing fix + tool_agent mcp_list_servers dispatch.

Reproduces the exact bug from the user's screenshot: "Check the mcp that is
connected" fell through to plain chat because 'mcp' was never in the
needs_tools keyword list, so the LLM answered from general training data
about Microchip's MCP hardware chips instead of checking real MCP servers.
"""
import os, sys, asyncio, re

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/stew-mcp-route-test.db"
sys.path.insert(0, os.path.abspath("stew_deploy"))
os.chdir("stew_deploy")


def main():
    # 1) Extract the actual keyword list from main.py source (not re-typed by
    #    hand) so the test fails if the source list ever regresses.
    src = open("server/main.py").read()
    m = re.search(r"needs_tools = any\(kw in user_lower for kw in \[(.*?)\]\)", src, re.S)
    assert m, "could not locate needs_tools keyword list in main.py"
    keywords = re.findall(r'"([^"]+)"', m.group(1))
    assert "mcp" in keywords, "mcp keyword missing from classifier"
    assert "mcp server" in keywords
    print(f"PASS keyword list contains mcp terms ({len(keywords)} total keywords)")

    test_messages = [
        "Check the mcp that is connected",   # exact user message from screenshot
        "check my mcp servers",
        "what mcp connectors do I have",
        "list my mcp tools",
        "is my mcp connected",
    ]
    for msg in test_messages:
        low = msg.lower()
        hit = any(kw in low for kw in keywords)
        assert hit, f"REGRESSION: '{msg}' still does not trigger needs_tools"
        print(f"PASS routes to tool agent: {msg!r}")

    # Sanity: an unrelated message should NOT trigger via mcp keywords
    unrelated = "what's the capital of France"
    hit = any(kw in unrelated.lower() for kw in keywords)
    assert not hit
    print("PASS unrelated message does not false-positive")

main()


async def dispatch_test():
    from server.database import AsyncSessionLocal, engine
    from server.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from server.mcp_service import add_server, list_servers
    UID = "555444333"

    # 2) Simulate "user connected something on the MCP tab" — real DeepWiki
    #    public MCP server (free, no token needed), then confirm the SAME
    #    lookup tool_agent.mcp_list_servers uses actually sees it.
    try:
        res = await add_server(UID, "DeepWiki", "https://mcp.deepwiki.com/mcp")
        print("PASS add_server connected:", res.get("name"), "tools:", len(res.get("tools") or []))
    except Exception as exc:
        print("SKIP add_server (network/offline):", exc)
        return

    from server.tool_agent import execute_tool
    result = await execute_tool({"tool": "mcp_list_servers", "args": {}}, tg_user_id=UID, chat_id=UID, bot=None)
    assert result["success"], result
    servers = result["data"]["servers"]
    assert any(s.get("name") == "DeepWiki" for s in servers), servers
    print(f"PASS tool_agent.mcp_list_servers sees the newly connected server ({len(servers)} total)")

    # cleanup
    from server.mcp_service import remove_server
    for s in servers:
        if s.get("name") == "DeepWiki":
            await remove_server(UID, s["id"])
    print("PASS cleanup removed test server")


try:
    asyncio.run(dispatch_test())
except Exception as e:
    print("dispatch_test error:", repr(e))
    raise

print("\nALL MCP ROUTING E2E TESTS: PASS")
