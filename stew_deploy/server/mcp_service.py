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

import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlencode

import httpx
from sqlalchemy import select, delete

from server.database import AsyncSessionLocal
from server.models import McpServer, McpOAuthState, PendingAgentAction
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


class McpAuthRequired(ValueError):
    """The server rejected us with 401/403 — carries the raw response so the
    caller can attempt real OAuth discovery instead of just failing."""
    def __init__(self, response: httpx.Response):
        super().__init__("Authentication required — this server needs OAuth sign-in.")
        self.response = response


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
            raise McpAuthRequired(resp)
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
        # Hardening: cap total payload size so a hostile/looping MCP server
        # can't balloon memory or the Telegram reply.
        content = res.get("content") or []
        if content and len(_content_to_text(content)) > 24000:
            content = [{"type": "text",
                        "text": _content_to_text(content)[:20000]
                                + "\n…(response truncated: MCP server returned an oversized payload)"}]
        structured = res.get("structuredContent")
        if structured and len(json.dumps(structured, default=str)) > 24000:
            structured = {"note": "structured content dropped: oversized payload"}
        return {"success": True, "content": content, "structuredContent": structured}


# ── Hardening: per-user MCP rate limit (token bucket) ───────────────────────
# 2026's MCP security flaws showed untrusted remote servers must be throttled.
# 30 calls per rolling hour per user — plenty for real work, stops runaway loops.
_MCP_RATE: Dict[str, list] = {}
MCP_RATE_LIMIT = 30
MCP_RATE_WINDOW = 3600.0


def _mcp_rate_check(user_id: str) -> bool:
    import time as _t
    key = str(user_id)
    now = _t.time()
    hits = [h for h in _MCP_RATE.get(key, []) if now - h < MCP_RATE_WINDOW]
    if len(hits) >= MCP_RATE_LIMIT:
        _MCP_RATE[key] = hits
        return False
    hits.append(now)
    _MCP_RATE[key] = hits
    return True


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


# ── OAuth 2.1 discovery, Dynamic Client Registration & PKCE ─────────────────
# Per the MCP Authorization spec (2025-06-18): a protected MCP server answers
# 401 with `WWW-Authenticate: Bearer resource_metadata="<url>"` (RFC 9728).
# That URL's JSON names the real Authorization Server(s); THAT server's own
# `.well-known/oauth-authorization-server` (RFC 8414) gives the actual
# authorize/token/registration endpoints. Dynamic Client Registration
# (RFC 7591) then gets Stew a client_id without any manual app-console setup
# — so "tap Connect" really does route the user through their provider's own
# login page, exactly like Composio's OAuth apps do.

def _redirect_uri() -> str:
    from server.config import settings
    base = (settings.APP_BASE_URL or "").rstrip("/")
    if not base:
        raise ValueError("Server misconfigured: APP_BASE_URL is not set, so OAuth callbacks have nowhere to land.")
    return base + "/api/mcp/oauth/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _discover_protected_resource(mcp_url: str, resp: httpx.Response) -> Dict[str, Any]:
    """RFC 9728: find the protected-resource metadata document for this MCP
    server, either from the 401's WWW-Authenticate header or the well-known
    fallback path on the same origin."""
    origin = f"{urlparse(mcp_url).scheme}://{urlparse(mcp_url).netloc}"
    meta_url = None
    www_auth = resp.headers.get("www-authenticate", "") if resp is not None else ""
    m = re.search(r'resource_metadata="([^"]+)"', www_auth)
    if m:
        meta_url = m.group(1)
    if not meta_url:
        meta_url = origin + "/.well-known/oauth-protected-resource"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(meta_url)
        if r.status_code >= 400:
            # Some servers publish the resource metadata at a path-suffixed
            # location (RFC 9728 §3.1) instead of the bare origin.
            path = urlparse(mcp_url).path.rstrip("/")
            if path:
                r = await client.get(origin + "/.well-known/oauth-protected-resource" + path)
        r.raise_for_status()
        return r.json()


async def _discover_authorization_server(as_issuer: str) -> Dict[str, Any]:
    """RFC 8414 (falls back to OIDC discovery) — authorize/token/registration
    endpoints for the authorization server that guards this MCP resource."""
    issuer = as_issuer.rstrip("/")
    candidates = [
        issuer + "/.well-known/oauth-authorization-server",
        issuer + "/.well-known/openid-configuration",
    ]
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        last_exc: Optional[Exception] = None
        for c in candidates:
            try:
                r = await client.get(c)
                if r.status_code < 400:
                    return r.json()
            except Exception as exc:
                last_exc = exc
        raise ValueError(f"Could not discover the authorization server's endpoints ({last_exc or 'no metadata found'}).")


async def _register_oauth_client(registration_endpoint: str, redirect_uri: str) -> Dict[str, Any]:
    """RFC 7591 Dynamic Client Registration — gets Stew a client_id (and
    client_secret, for servers that issue one) with no manual setup."""
    payload = {
        "client_name": "S.T.E.W Agent",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(registration_endpoint, json=payload)
        r.raise_for_status()
        return r.json()


async def start_oauth_connect(user_id: str, name: str, url: str, resp: httpx.Response) -> Dict[str, Any]:
    """Called when a plain connect attempt hits 401/403. Runs the full
    discovery -> DCR -> PKCE chain and returns an authorize_url for the user
    to open — completing the sign-in lands on our callback and finishes
    the connection automatically."""
    resource_meta = await _discover_protected_resource(url, resp)
    as_list = resource_meta.get("authorization_servers") or []
    if not as_list:
        raise ValueError("This server's protected-resource metadata lists no authorization server.")
    as_meta = await _discover_authorization_server(str(as_list[0]))
    authorize_endpoint = as_meta.get("authorization_endpoint")
    token_endpoint = as_meta.get("token_endpoint")
    if not authorize_endpoint or not token_endpoint:
        raise ValueError("The authorization server's metadata is missing authorize/token endpoints.")
    redirect_uri = _redirect_uri()
    client_id = None
    client_secret = None
    reg_endpoint = as_meta.get("registration_endpoint")
    if reg_endpoint:
        try:
            reg = await _register_oauth_client(reg_endpoint, redirect_uri)
            client_id = reg.get("client_id")
            client_secret = reg.get("client_secret")
        except Exception as exc:
            logger.warning("MCP dynamic client registration failed: %s", exc)
    if not client_id:
        raise ValueError(
            "This server needs OAuth sign-in but doesn't support automatic client "
            "registration. Ask the provider for a client_id (and add it as the "
            "server's access token field) or use their manual API token instead."
        )
    scope = " ".join(as_meta.get("scopes_supported") or []) or None
    verifier, challenge = _pkce_pair()
    state_row = McpOAuthState(
        telegram_user_id=str(user_id), name=(name or "My MCP")[:MAX_NAME_LEN], url=url,
        authorization_endpoint=authorize_endpoint, token_endpoint=token_endpoint,
        client_id=client_id, client_secret=client_secret, code_verifier=verifier,
        redirect_uri=redirect_uri, resource=resource_meta.get("resource") or url, scope=scope,
    )
    async with AsyncSessionLocal() as db:
        db.add(state_row)
        await db.commit()
        await db.refresh(state_row)
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state_row.id, "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": state_row.resource,
    }
    if scope:
        params["scope"] = scope
    authorize_url = authorize_endpoint + ("&" if "?" in authorize_endpoint else "?") + urlencode(params)
    return {"success": False, "oauth_required": True, "authorize_url": authorize_url,
            "message": "This MCP server needs you to sign in with your own account. "
                       "Open the link, approve access, and I'll finish connecting automatically."}


async def complete_oauth_callback(state_id: str, code: str) -> Dict[str, Any]:
    """Provider redirected the browser back here with an authorization code.
    Exchange it for tokens, then create (or refresh) the real McpServer row
    and do the first tool sync."""
    async with AsyncSessionLocal() as db:
        state_row = (await db.execute(select(McpOAuthState).where(
            McpOAuthState.id == state_id, McpOAuthState.status == "pending"
        ))).scalars().first()
        if not state_row:
            return {"success": False, "error": "This connection link already expired or was used. Start over in the Mini App."}
        state_row.status = "used"
        await db.commit()

    token_payload = {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": state_row.redirect_uri, "client_id": state_row.client_id,
        "code_verifier": state_row.code_verifier, "resource": state_row.resource,
    }
    if state_row.client_secret:
        token_payload["client_secret"] = state_row.client_secret
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(state_row.token_endpoint, data=token_payload,
                              headers={"Accept": "application/json"})
    if r.status_code >= 400:
        return {"success": False, "error": f"Token exchange failed (HTTP {r.status_code}): {(r.text or '')[:300]}"}
    tokens = r.json()
    access_token = tokens.get("access_token")
    if not access_token:
        return {"success": False, "error": "Provider did not return an access token."}

    dup = await list_servers(state_row.telegram_user_id)
    existing = next((s for s in dup if s["url"] == state_row.url), None)
    async with AsyncSessionLocal() as db:
        if existing:
            row = await get_server(state_row.telegram_user_id, existing["id"])
        else:
            row = McpServer(telegram_user_id=state_row.telegram_user_id, name=state_row.name, url=state_row.url)
            db.add(row)
        row.auth_header_name = "Authorization"
        row.auth_token = access_token
        row.oauth_authorization_endpoint = state_row.authorization_endpoint
        row.oauth_token_endpoint = state_row.token_endpoint
        row.oauth_client_id = state_row.client_id
        row.oauth_client_secret = state_row.client_secret
        row.oauth_refresh_token = tokens.get("refresh_token")
        row.oauth_scope = state_row.scope
        row.oauth_resource = state_row.resource
        row.status = "pending"
        await db.commit()
        await db.refresh(row)
    sync_result = await sync_server(state_row.telegram_user_id, row.id)
    sync_result["server_id"] = row.id
    sync_result["server_name"] = row.name
    return sync_result


async def _refresh_oauth_token(row: McpServer) -> bool:
    """Silently renew an expired access token before surfacing an auth error
    to the user — mirrors how Composio's own OAuth apps behave."""
    if not row.oauth_refresh_token or not row.oauth_token_endpoint:
        return False
    payload = {"grant_type": "refresh_token", "refresh_token": row.oauth_refresh_token,
               "client_id": row.oauth_client_id}
    if row.oauth_client_secret:
        payload["client_secret"] = row.oauth_client_secret
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(row.oauth_token_endpoint, data=payload, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            return False
        tokens = r.json()
        if not tokens.get("access_token"):
            return False
        async with AsyncSessionLocal() as db:
            row.auth_token = tokens["access_token"]
            if tokens.get("refresh_token"):
                row.oauth_refresh_token = tokens["refresh_token"]
            db.add(row)
            await db.commit()
        return True
    except Exception as exc:
        logger.warning("MCP OAuth token refresh failed for %s: %s", row.name, exc)
        return False


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
    if not auth_token:
        # No token was supplied — probe the server first. If it needs real
        # sign-in, route through OAuth discovery instead of saving a broken
        # "error" row and telling the user to paste a token they don't have.
        try:
            probe = McpClient(url, auth_header_name, None)
            await probe.connect()
        except McpAuthRequired as exc:
            return await start_oauth_connect(user_id, name, url, exc.response)
        except Exception:
            pass  # any other failure surfaces normally from sync_server below
    row = McpServer(telegram_user_id=str(user_id), name=name, url=url,
                    auth_header_name=(auth_header_name or "Authorization")[:64],
                    auth_token=auth_token or None, status="pending")
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    result = await sync_server(user_id, row.id)
    # Security notice for custom servers (Stew doesn't verify third-party MCPs)
    if isinstance(result, dict):
        result["security_note"] = ("Custom MCP servers are NOT verified by Stew — only connect servers you trust. "
                                   "Review the tools and permissions it asks for; you can remove it anytime in the ⚡MCP tab.")
    return result


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
    except McpAuthRequired:
        # An OAuth access token can expire between syncs. Try a silent
        # refresh_token renewal first (no user interaction) before asking
        # them to reconnect through the browser again.
        if await _refresh_oauth_token(row):
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
            except Exception as exc2:
                msg = str(exc2)[:500]
        else:
            msg = "Your sign-in expired and couldn't be silently renewed. Remove and reconnect this server."
        async with AsyncSessionLocal() as db:
            row.status = "error"
            row.last_error = msg
            row.tool_count = 0
            db.add(row)
            await db.commit()
        return {"success": False, "status": "error", "error": msg, "needs_reauth": True}
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

    # Hardening: rolling per-user rate limit (stops runaway loops / hostile servers)
    if not _mcp_rate_check(stable_user_id):
        return {"success": False,
                "error": "MCP rate limit reached (30 calls/hour). Try again later — this protects you from runaway loops and untrusted servers."}

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
    except McpAuthRequired:
        if await _refresh_oauth_token(row):
            try:
                client = McpClient(row.url, row.auth_header_name, row.auth_token)
                await client.connect()
                result = await client.call_tool(tool_name, arguments)
            except Exception as exc2:
                logger.warning("MCP call failed after token refresh (%s/%s): %s", row.name, tool_name, exc2)
                try:
                    await record_activity(stable_user_id, slug, "failed", arguments)
                except Exception:
                    pass
                return {"success": False, "error": f"MCP call failed: {exc2}"}
        else:
            try:
                await record_activity(stable_user_id, slug, "failed", arguments)
            except Exception:
                pass
            return {"success": False, "error": "Your sign-in expired and couldn't be renewed. Reconnect this server in the Mini App.", "needs_reauth": True}
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
    except McpAuthRequired as exc:
        if auth_token:
            return {"success": False, "error": "That token was rejected — check it, or leave it blank to sign in with OAuth instead."}
        try:
            return await start_oauth_connect(user_id, "My MCP", url, exc.response)
        except Exception as exc2:
            return {"success": False, "error": f"This server needs sign-in and OAuth discovery failed: {exc2}"[:400]}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:400]}
