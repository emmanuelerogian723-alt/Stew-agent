"""
S.T.E.W MCP Service — remote Model Context Protocol connectors.

Closes the biggest gap from the Claude/ChatGPT/Zapier competitor study: their
connector ecosystems accept ANY remote MCP server, while Stew was locked to
Composio's fixed catalog. With this module a user can point Stew at any
public (or authenticated) MCP endpoint — Higgsfield for AI video, Clay for
lead enrichment, a company-internal CRM MCP, anything — and Stew discovers
its tools and executes them under the exact same trust model as connected
apps:

  read-only tools        -> run automatically
  private writes         -> run immediately (the chat request IS approval)
  destructive / public   -> same chat-native Approve/Cancel button + queue
                            the Composio actions use (human-in-the-loop)

Transport: MCP Streamable HTTP (2025-03-26 spec) — JSON-RPC 2.0 POSTs with
Accept: application/json, text/event-stream; server responses may arrive as
plain JSON or as SSE `data:` frames, so both are parsed. The optional
`mcp-session-id` response header is captured and echoed on subsequent calls.
Stdio servers can't be supported from a hosted bot — remote HTTP only.

Every executed call meters the same `connector_action` monthly allowance the
Composio path uses, so free-tier limits and paywall behavior stay identical.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, delete

from server.database import AsyncSessionLocal
from server.models import McpServer, PendingAgentAction
from server.agent_activity import (
    queue_approval,
    record_activity,
    action_summary,
)

logger = logging.getLogger(__name__)

RPC_VERSION = "2025-03-26"
MAX_TOOLS = 300
MAX_URL_LEN = 500
MAX_NAME_LEN = 80
CALL_TIMEOUT = 90.0
INIT_TIMEOUT = 20.0

_MCP_DESTRUCTIVE_WORDS = {
    "DELETE", "REMOVE", "DESTROY", "PURGE", "WIPE", "RESET",
}
_MCP_PUBLIC_WORDS = {
    "PUBLISH", "POST", "TWEET", "BROADCAST", "ANNOUNCE", "SHARE", "UPLOAD",
    "SEND_PUBLIC",
}
_MCP_WRITE_WORDS = {
    "CREATE", "UPDATE", "SEND", "WRITE", "ADD", "SET", "INSERT", "UPSERT",
    "PATCH", "MOVE", "APPLY", "EXECUTE", "RUN", "GENERATE", "MAKE", "BUILD",
    "TRIGGER", "SCHEDULE", "BOOK", "RESERVE", "ORDER", "PAY",
    "SUBMIT", "PUBLISH", "POST", "UPLOAD", "DELETE", "REMOVE",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def slug_for(server_id: str, tool_name: str) -> str:
    """Pending-action slug: 'MCP__<server_id>__<tool_name>'. toolkit_from_slug
    derives 'mcp', and approve_pending_action dispatches on that to this
    module instead of the Composio executor."""
    return f"MCP__{server_id}__{tool_name}"


def parse_mcp_slug(tool_slug: str) -> Optional[tuple[str, str]]:
    parts = (tool_slug or "").split("__", 2)
    if len(parts) == 3 and parts[0].upper() == "MCP":
        return parts[1], parts[2]
    return None


# ── transport ───────────────────────────────────────────────────────────────

def _parse_response(resp: httpx.Response) -> Dict[str, Any]:
    """Accept both plain-JSON and text/event-stream MCP replies."""
    ctype = (resp.headers.get("content-type") or "").lower()
    body = resp.text or ""
    if "text/event-stream" in ctype or (not body.strip().startswith("{")):
        payload: Optional[Dict[str, Any]] = None
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                # The last JSON-RPC response object in the stream wins.
                if isinstance(parsed, dict) and ("id" in parsed or "result" in parsed or "error" in parsed):
                    payload = parsed
        if payload is not None:
            return payload
    try:
        return resp.json()
    except Exception:
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32603, "message": f"Non-JSON MCP reply ({resp.status_code}): {body[:200]}"}}


class McpClient:
    """Minimal Streamable HTTP JSON-RPC client for one server connection."""

    def __init__(self, url: str, auth_header_name: Optional[str] = None,
                 auth_token: Optional[str] = None):
        self.url = url
        self.session_id: Optional[str] = None
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if auth_token:
            name = (auth_header_name or "Authorization").strip() or "Authorization"
            value = auth_token
            if name.lower() == "authorization" and not re.match(r"^\w+ ", value):
                value = f"Bearer {value}"
            headers[name] = value
        self.headers = headers

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
                   timeout: float = INIT_TIMEOUT, msg_id: int = 1) -> Dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = dict(self.headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(self.url, json=payload, headers=headers)
        if resp.status_code in (404, 405, 501):
            raise ValueError(
                f"Endpoint does not speak Streamable HTTP (HTTP {resp.status_code}). "
                "Stdio/other transports are not supported."
            )
        if resp.status_code == 401 or resp.status_code == 403:
            raise ValueError("Authentication failed — check the server's access token.")
        if resp.status_code >= 400:
            raise ValueError(f"HTTP {resp.status_code}: {(resp.text or '')[:200]}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return _parse_response(resp)

    async def _notify_initialized(self) -> None:
        headers = dict(self.headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                await client.post(self.url, json={"jsonrpc": "2.0",
                                                  "method": "notifications/initialized"},
                                  headers=headers)
        except Exception:
            pass  # notification best-effort

    async def connect(self) -> None:
        result = await self._rpc("initialize", {
            "protocolVersion": RPC_VERSION,
            "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
            "clientInfo": {"name": "stew-agent", "version": "1.0"},
        })
        if "error" in result:
            raise ValueError(f"Initialize failed: {result['error'].get('message')}")
        await self._notify_initialized()

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list", {}, msg_id=2)
        if "error" in result:
            raise ValueError(f"tools/list failed: {result['error'].get('message')}")
        tools = (result.get("result") or {}).get("tools") or []
        return tools[:MAX_TOOLS]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}},
                                 timeout=CALL_TIMEOUT, msg_id=3)
        if "error" in result:
            err = result["error"]
            return {"success": False, "error": f"MCP error {err.get('code')}: {err.get('message')}"}
        res = result.get("result") or {}
        if res.get("isError"):
            return {"success": False, "error": _content_to_text(res.get("content")) or "The tool reported an error."}
        return {"success": True, "content": res.get("content") or [],
                "structuredContent": res.get("structuredContent")}


def _content_to_text(content: Any) -> str:
    parts = []
    for item in content or []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, default=str)[:500])
        else:
            parts.append(str(item)[:500])
    return "\n".join(parts)[:20000]


# ── classification (mirrors the Composio risk tiers) ────────────────────────

def classify_mcp_tool(tool: Dict[str, Any]) -> str:
    """Return 'read_only', 'write', 'destructive', or 'public'."""
    name = (tool.get("name") or "").upper()
    desc = (tool.get("description") or "").upper()
    blob_words = set(re.findall(r"[A-Z0-9]+", name + " " + desc))
    if blob_words & _MCP_DESTRUCTIVE_WORDS:
        return "destructive"
    if blob_words & _MCP_PUBLIC_WORDS:
        return "public"
    if blob_words & _MCP_WRITE_WORDS:
        return "write"
    return "read_only"


# ── always-allow permissions ────────────────────────────────────────────────

async def get_always_allow(user_id: str, tool_key: str) -> bool:
    from server.models import ToolPermission
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ToolPermission).where(
            ToolPermission.telegram_user_id == str(user_id),
            ToolPermission.tool_key == tool_key))).scalars().first()
        return bool(row and row.allow_always)


async def list_trusted_tools(user_id: str) -> List[Dict[str, Any]]:
    from server.models import ToolPermission
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(ToolPermission).where(
            ToolPermission.telegram_user_id == str(user_id),
            ToolPermission.allow_always.is_(True)))).scalars().all()
        return [{"tool_key": r.tool_key,
                 "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None}
                for r in rows]


async def set_always_allow(user_id: str, tool_key: str, allow: bool) -> None:
    from server.models import ToolPermission
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ToolPermission).where(
            ToolPermission.telegram_user_id == str(user_id),
            ToolPermission.tool_key == tool_key))).scalars().first()
        if row:
            row.allow_always = bool(allow)
        else:
            db.add(ToolPermission(telegram_user_id=str(user_id),
                                  tool_key=tool_key, allow_always=bool(allow)))
        await db.commit()


# ── server CRUD ─────────────────────────────────────────────────────────────

def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LEN:
        raise ValueError("A server URL is required (max 500 chars).")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must be a full http(s) endpoint, e.g. https://host/mcp")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("Only https:// MCP endpoints are allowed (localhost for dev).")
    return url


async def add_server(user_id: str, name: str, url: str,
                     auth_header_name: Optional[str] = None,
                     auth_token: Optional[str] = None) -> Dict[str, Any]:
    name = (name or "").strip()[:MAX_NAME_LEN] or "My MCP server"
    url = _validate_url(url)
    dup = await list_servers(user_id)
    if any(s["url"] == url for s in dup):
        raise ValueError("That MCP server URL is already connected.")
    if len(dup) >= 10:
        raise ValueError("Up to 10 MCP servers per user. Remove one first.")
    row = McpServer(telegram_user_id=str(user_id), name=name, url=url,
                    auth_header_name=(auth_header_name or "Authorization")[:64],
                    auth_token=auth_token or None, status="pending")
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return await sync_server(user_id, row.id)


async def remove_server(user_id: str, server_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(delete(McpServer).where(
            McpServer.telegram_user_id == str(user_id),
            McpServer.id == server_id))
        await db.commit()
        return bool(result.rowcount)


async def list_servers(user_id: str, include_secret: bool = False) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(McpServer).where(
            McpServer.telegram_user_id == str(user_id)
        ).order_by(McpServer.created_at.desc()))).scalars().all()
        out = []
        for r in rows:
            item = {
                "id": r.id, "name": r.name, "url": r.url,
                "status": r.status, "tool_count": r.tool_count,
                "last_synced_at": r.last_synced_at.isoformat() + "Z" if r.last_synced_at else None,
                "last_error": r.last_error,
                "auth_header_name": r.auth_header_name,
                "has_token": bool(r.auth_token),
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            }
            if include_secret:
                item["auth_token"] = r.auth_token
            out.append(item)
        return out


async def get_server(user_id: str, server_id: str) -> Optional[McpServer]:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(McpServer).where(
            McpServer.telegram_user_id == str(user_id),
            McpServer.id == server_id))).scalars().first()
        return row


async def sync_server(user_id: str, server_id: str) -> Dict[str, Any]:
    """Connect (or reconnect), refresh the cached tool list, store status."""
    row = await get_server(user_id, server_id)
    if not row:
        raise ValueError("MCP server not found.")
    try:
        client = McpClient(row.url, row.auth_header_name, row.auth_token)
        await client.connect()
        tools = await client.list_tools()
        async with AsyncSessionLocal() as db:
            row.status = "active"
            row.last_error = None
            row.tools = tools
            row.tool_count = len(tools)
            row.last_synced_at = _utcnow()
            db.add(row)
            await db.commit()
        return {"success": True, "status": "active", "tool_count": len(tools),
                "tools": [_tool_public(t) for t in tools]}
    except Exception as exc:
        msg = str(exc)[:500]
        async with AsyncSessionLocal() as db:
            row.status = "error"
            row.last_error = msg
            row.tool_count = 0
            db.add(row)
            await db.commit()
        return {"success": False, "status": "error", "error": msg}


def _tool_public(tool: Dict[str, Any]) -> Dict[str, Any]:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    return {
        "name": tool.get("name"),
        "description": (tool.get("description") or "")[:400],
        "permission": classify_mcp_tool(tool),
        "required_fields": [str(x) for x in required][:10],
        "parameters": [
            {"name": str(k), "type": (v or {}).get("type", "string"),
             "description": ((v or {}).get("description") or "")[:200],
             "required": str(k) in required}
            for k, v in list(props.items())[:20]
        ],
    }


async def list_server_tools(user_id: str, server_id: str,
                           refresh: bool = False) -> Dict[str, Any]:
    row = await get_server(user_id, server_id)
    if not row:
        return {"success": False, "error": "MCP server not found."}
    if refresh or not row.tools or row.status != "active":
        return await sync_server(user_id, server_id)
    return {"success": True, "status": row.status,
            "tool_count": row.tool_count,
            "tools": [_tool_public(t) for t in (row.tools or [])]}


async def find_tool(user_id: str, server_id: str, tool_name: str) -> Optional[Dict[str, Any]]:
    """Return the raw cached tool entry (with inputSchema) by name."""
    row = await get_server(user_id, server_id)
    if not row:
        return None
    for t in row.tools or []:
        if t.get("name") == tool_name:
            return t
    return None


async def search_tools(user_id: str, query: str = "") -> List[Dict[str, Any]]:
    """Search across ALL the user's connected MCP servers (Stew's agent uses
    this to discover capabilities, mirroring composio_search_tools)."""
    servers = await list_servers(user_id)
    q = (query or "").lower().strip()
    hits: List[Dict[str, Any]] = []
    for s in servers:
        if s["status"] != "active":
            continue
        row = await get_server(user_id, s["id"])
        for t in row.tools or []:
            name = (t.get("name") or "").lower()
            desc = (t.get("description") or "").lower()
            if not q or q in name or q in desc:
                hits.append({**_tool_public(t),
                             "server_id": s["id"], "server_name": s["name"]})
                if len(hits) >= 25:
                    return hits
    return hits


# ── execution (approval-gated, metered) ─────────────────────────────────────

async def execute_mcp_tool(user_id: str, server_id: str, tool_name: str,
                           arguments: Dict[str, Any],
                           approved: bool = False) -> Dict[str, Any]:
    """The MCP twin of composio_service.execute_action — same metering, same
    risk-tier gating, same approval queue, so free-tier limits, the chat
    Approve/Cancel buttons, and the Mini App queue all work identically."""
    stable_user_id = str(user_id or "anonymous")
    tool_name = (tool_name or "").strip()
    if not tool_name or not isinstance(arguments, dict):
        return {"success": False, "error": "A tool name and a JSON arguments object are required."}

    row = await get_server(stable_user_id, server_id)
    if not row:
        return {"success": False, "error": "MCP server not found. Connect it in the Mini App first."}
    if row.status != "active":
        return {"success": False, "error": f"MCP server is not connected (status: {row.status}). "
                                           f"{row.last_error or 'Re-sync it in the Mini App.'}"}

    tool = await find_tool(stable_user_id, server_id, tool_name)
    if tool is None:
        return {"success": False, "error": f"Tool '{tool_name}' was not found on {row.name}. Re-sync the server."}

    tier = classify_mcp_tool(tool)
    tool_key = f"mcp:{server_id}:{tool_name}"
    slug = slug_for(server_id, tool_name)
    needs_approval = tier in ("destructive", "public")

    # Per-user "Always allow" trust toggle (Claude-style permission model).
    if needs_approval and await get_always_allow(stable_user_id, tool_key):
        needs_approval = False
        tier = "trusted"

    # Same monthly allowance as connected-app actions — MCP is not a metering
    # loophole. Pending approvals don't meter; only real executions do.
    if approved is not True and tier != "trusted":
        try:
            from server.paywall import metered_feature_gate
            gate = await metered_feature_gate(stable_user_id, "connector_action")
            if not gate.get("allowed"):
                return {"success": False, "paywall": True,
                        "error": gate.get("message"), "message": gate.get("message")}
        except Exception as exc:
            logger.warning("MCP metering skipped: %s", exc)

    if needs_approval and not approved:
        pending = await queue_approval(stable_user_id, slug, arguments)
        try:
            await record_activity(stable_user_id, slug, "awaiting_approval", arguments,
                                  approval_required=True)
        except Exception as exc:
            logger.warning("MCP approval audit write failed: %s", exc)
        return {
            "success": False, "approval_required": True, "approval_id": pending.id,
            "kind": "destructive" if tier == "destructive" else "publish",
            "tool_slug": slug, "summary": pending.summary,
            "server": row.name, "tool": tool_name,
            "message": ("This MCP tool permanently deletes/removes something and can't be undone."
                        if tier == "destructive" else
                        "This MCP tool publishes/posts something publicly where others will see it.")
                       + " I've sent an Approve/Cancel button in the chat — tap it and I'll continue.",
        }

    try:
        client = McpClient(row.url, row.auth_header_name, row.auth_token)
        await client.connect()
        result = await client.call_tool(tool_name, arguments)
    except Exception as exc:
        logger.warning("MCP call failed (%s/%s): %s", row.name, tool_name, exc)
        try:
            await record_activity(stable_user_id, slug, "failed", arguments)
        except Exception:
            pass
        return {"success": False, "error": f"MCP call failed: {exc}"}

    status = "completed" if result.get("success") else "failed"
    try:
        await record_activity(stable_user_id, slug, status, arguments)
    except Exception as exc:
        logger.warning("MCP activity write failed: %s", exc)
    if result.get("success"):
        result["text"] = _content_to_text(result.get("content"))
        if result.get("structuredContent"):
            result["data"] = result["structuredContent"]
    result["server"] = row.name
    result["tool"] = tool_name
    return result


# ── integration with the shared approval queue ──────────────────────────────

async def resume_approved_mcp(user_id: str, pending: PendingAgentAction) -> Dict[str, Any]:
    """Called from composio_service.approve_pending_action when the claimed
    pending row is an MCP action (toolkit 'mcp')."""
    parsed = parse_mcp_slug(pending.tool_slug)
    if not parsed:
        return {"success": False, "error": "Malformed MCP approval reference."}
    server_id, tool_name = parsed
    args = dict(pending.arguments or {})
    args.pop("_mcp_server", None)
    return await execute_mcp_tool(user_id, server_id, tool_name, args, approved=True)


# ── connectivity test (Mini App "Test connection") ──────────────────────────

async def test_server(user_id: str, url: str, auth_header_name: Optional[str],
                      auth_token: Optional[str]) -> Dict[str, Any]:
    """Try initialize + tools/list WITHOUT saving; used by the Mini App form."""
    url = _validate_url(url)
    try:
        client = McpClient(url, auth_header_name, auth_token)
        await client.connect()
        tools = await client.list_tools()
        return {"success": True, "tool_count": len(tools),
                "sample": [t.get("name") for t in tools[:8]]}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:400]}
