"""Local end-to-end test: MCP connectors + Always-allow trust toggles.

Runs a real MCP server (Streamable HTTP, JSON-RPC 2.0) in-process on localhost,
then drives the FULL production code path: add_server → sync/classify →
search → read execute (instant) → publish execute (approval queue) → chat
approve dispatcher → always-allow promotion → trust list → revoke → remove.
"""
import os, sys, asyncio, json, threading

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/stew-mcp-test.db"
sys.path.insert(0, os.path.abspath("stew_deploy"))
os.chdir("stew_deploy")

TOOLS = [
    {"name": "get_weather", "description": "Get current weather for a city (read-only lookup)",
     "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}},
    {"name": "create_invoice", "description": "Create a draft invoice for a customer",
     "inputSchema": {"type": "object", "properties": {"customer": {"type": "string"}, "amount": {"type": "number"}}, "required": ["customer", "amount"]}},
    {"name": "delete_record", "description": "Permanently delete a CRM record by id — cannot be undone",
     "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"]}},
    {"name": "publish_post", "description": "Publish a post publicly to followers timeline",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]
EXECUTED = []


def start_mock_mcp(port):
    """Tiny threaded HTTP server speaking Streamable HTTP JSON-RPC."""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = body.get("method")
            if method == "initialize":
                resp = {"jsonrpc": "2.0", "id": body["id"], "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock-crm", "version": "1.0"}}}
            elif method == "tools/list":
                resp = {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": TOOLS}}
            elif method == "tools/call":
                name = body["params"]["name"]
                args = body["params"].get("arguments") or {}
                EXECUTED.append((name, args))
                resp = {"jsonrpc": "2.0", "id": body["id"], "result": {
                    "content": [{"type": "text", "text": f"OK: {name} executed with {json.dumps(args)}"}],
                    "isError": False}}
            else:
                resp = {"jsonrpc": "2.0", "id": body.get("id"), "result": {}}
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


async def main():
    from server.database import AsyncSessionLocal, engine
    from server.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import server.mcp_service as mcp
    from server.composio_service import approve_pending_action
    from server.agent_activity import pending_dashboard
    from server.mcp_service import classify_mcp_tool

    # ── classification sanity
    assert classify_mcp_tool(TOOLS[0]) == "read_only"
    assert classify_mcp_tool(TOOLS[1]) == "write"
    assert classify_mcp_tool(TOOLS[2]) == "destructive"
    assert classify_mcp_tool(TOOLS[3]) == "public"
    print("PASS classify: read/write/destructive/public")

    UID = "999888777"
    srv = start_mock_mcp(8765)
    url = "http://127.0.0.1:8765/mcp"

    # 1) reject insecure https-rule violations & bad URLs
    for bad in ["ftp://x", "http://example.com/mcp", "not-a-url", ""]:
        try:
            await mcp.add_server(UID, "bad", bad)
            raise AssertionError(f"should have rejected {bad}")
        except ValueError:
            pass
    print("PASS url validation rejects bad URLs")

    # 2) add server (connects immediately, caches tools)
    r = await mcp.add_server(UID, "Mock CRM", url)
    assert r["success"] and r["tool_count"] == 4, r
    print("PASS add_server connected, 4 tools cached")

    # 3) list servers
    servers = await mcp.list_servers(UID)
    assert servers[0]["status"] == "active" and servers[0]["tool_count"] == 4
    sid = servers[0]["id"]
    print("PASS list_servers active")

    # 4) search tools
    hits = await mcp.search_tools(UID, "post")
    assert len(hits) == 1 and hits[0]["name"] == "publish_post" and hits[0]["server_id"] == sid
    print("PASS search_tools finds publish_post on right server")

    # 5) read-only executes immediately
    r = await mcp.execute_mcp_tool(UID, sid, "get_weather", {"city": "Nsukka"})
    assert r["success"], r
    print("PASS read tool instant:", r.get("text", "")[:60])

    # 6) public tool pauses into the approval queue
    r = await mcp.execute_mcp_tool(UID, sid, "publish_post", {"text": "Stew x MCP!"})
    assert r.get("approval_required") and r.get("kind") == "publish" and "Approve/Cancel button" in r.get("message", ""), r
    pend_id = r["approval_id"]
    dash = await pending_dashboard(UID)
    assert any(x["id"] == pend_id and x["toolkit"] == "mcp" for x in dash), dash
    print("PASS publish tool queued (toolkit=mcp), id:", pend_id[:8])

    # 7) chat Approve tap routes through the shared dispatcher to MCP executor
    result = await approve_pending_action(UID, pend_id)
    assert result.get("success"), result
    assert ("publish_post", {"text": "Stew x MCP!"}) in EXECUTED, EXECUTED
    print("PASS approve dispatcher executed MCP tool")

    # 8) replay blocked
    result2 = await approve_pending_action(UID, pend_id)
    assert not result2.get("success"), result2
    print("PASS replay refused:", result2.get("error"))

    # 9) Always allow: promote publish_post, next run skips approval
    await mcp.set_always_allow(UID, f"mcp:{sid}:publish_post", True)
    r = await mcp.execute_mcp_tool(UID, sid, "publish_post", {"text": "trusted run"})
    assert r.get("success") and not r.get("approval_required"), r
    assert ("publish_post", {"text": "trusted run"}) in EXECUTED
    print("PASS always-allow skips approval and executes")

    # 10) destructive tool still pauses; trust list + revoke
    r = await mcp.execute_mcp_tool(UID, sid, "delete_record", {"record_id": "r1"})
    assert r.get("approval_required") and r.get("kind") == "destructive"
    trusted = await mcp.list_trusted_tools(UID)
    assert len(trusted) == 1 and trusted[0]["tool_key"] == f"mcp:{sid}:publish_post"
    await mcp.set_always_allow(UID, f"mcp:{sid}:publish_post", False)
    trusted = await mcp.list_trusted_tools(UID)
    assert len(trusted) == 0
    print("PASS destructive still pauses; trust list lists + revokes")

    # 11) composio-style always-allow key check (composio path uses same store)
    ok = await mcp.get_always_allow(UID, "composio:TWITTER_CREATE_TWEET")
    assert ok is False
    print("PASS composio tool keys default to not trusted")

    # 12) remove server
    assert await mcp.remove_server(UID, sid)
    assert (await mcp.list_servers(UID)) == []
    print("PASS remove_server")

    srv.shutdown()
    print("\nALL MCP E2E TESTS: PASS")

asyncio.run(main())
