/**
 * STEW Connectors Bridge
 * ─────────────────────────────────────────────────────────────────
 * Gives STEW API access to 45+ real OAuth-powered app integrations.
 * Developers call this with their STEW api_key + connector + action.
 *
 * Connectors powered by Base44 OAuth:
 * gmail, github, googlesheets, googledocs, googledrive, googlecalendar,
 * slack, notion, airtable, discord, dropbox, linear, hubspot + more
 * ─────────────────────────────────────────────────────────────────
 */

import { createClientFromRequest } from 'npm:@base44/sdk@0.8.31';

const CONNECTORS_BASE_URL = "https://stew-agent.onrender.com";

// Which plan can use which connectors
const PLAN_CONNECTOR_ACCESS: Record<string, string[]> = {
  free:       ["github", "weather", "search"],
  starter:    ["github", "gmail", "googlesheets", "googledrive", "googledocs", "discord", "airtable"],
  pro:        ["github", "gmail", "googlesheets", "googledrive", "googledocs", "googlecalendar",
               "discord", "airtable", "notion", "slack", "linear", "dropbox", "hubspot",
               "googletasks", "googleslides"],
  enterprise: ["*"], // all connectors
};

function canAccessConnector(plan: string, connector: string): boolean {
  const access = PLAN_CONNECTOR_ACCESS[plan] || PLAN_CONNECTOR_ACCESS["free"];
  return access.includes("*") || access.includes(connector);
}

// ── CONNECTOR ACTION HANDLERS ─────────────────────────────────

async function handleGmail(action: string, params: any, token: string) {
  const base = "https://gmail.googleapis.com/gmail/v1/users/me";
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  if (action === "list_emails") {
    const maxResults = params.max_results || 10;
    const q = params.query || "";
    const r = await fetch(`${base}/messages?maxResults=${maxResults}&q=${encodeURIComponent(q)}`, { headers: h });
    const data = await r.json();
    const messages = data.messages || [];

    // Fetch subject + snippet for each
    const previews = await Promise.all(messages.slice(0, 5).map(async (m: any) => {
      const msg = await fetch(`${base}/messages/${m.id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From`, { headers: h });
      const d = await msg.json();
      const subjectH = d.payload?.headers?.find((hh: any) => hh.name === "Subject");
      const fromH = d.payload?.headers?.find((hh: any) => hh.name === "From");
      return { id: m.id, subject: subjectH?.value || "(no subject)", from: fromH?.value || "unknown", snippet: d.snippet || "" };
    }));

    return { success: true, connector: "gmail", action, total: messages.length, emails: previews };
  }

  if (action === "send_email") {
    const { to, subject, body } = params;
    if (!to || !subject || !body) return { success: false, error: "to, subject, and body are required" };
    const raw = btoa(`To: ${to}\r\nSubject: ${subject}\r\nContent-Type: text/plain\r\n\r\n${body}`)
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const r = await fetch(`${base}/messages/send`, {
      method: "POST", headers: h, body: JSON.stringify({ raw }),
    });
    const data = await r.json();
    return { success: !!data.id, connector: "gmail", action, message_id: data.id, to, subject };
  }

  if (action === "read_email") {
    const { message_id } = params;
    if (!message_id) return { success: false, error: "message_id required" };
    const r = await fetch(`${base}/messages/${message_id}?format=full`, { headers: h });
    const data = await r.json();
    const subjectH = data.payload?.headers?.find((hh: any) => hh.name === "Subject");
    const fromH = data.payload?.headers?.find((hh: any) => hh.name === "From");
    // Extract body
    let body = data.snippet || "";
    try {
      const part = data.payload?.parts?.find((p: any) => p.mimeType === "text/plain");
      if (part?.body?.data) body = atob(part.body.data.replace(/-/g, "+").replace(/_/g, "/"));
    } catch {}
    return { success: true, connector: "gmail", action, subject: subjectH?.value, from: fromH?.value, body, snippet: data.snippet };
  }

  return { success: false, error: `Gmail action '${action}' not supported. Use: list_emails, send_email, read_email` };
}

async function handleGitHub(action: string, params: any, token: string) {
  const base = "https://api.github.com";
  const h = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "Content-Type": "application/json" };

  if (action === "list_repos") {
    const r = await fetch(`${base}/user/repos?sort=updated&per_page=${params.limit || 10}`, { headers: h });
    const data = await r.json();
    return { success: true, connector: "github", action, repos: data.map((repo: any) => ({ name: repo.full_name, description: repo.description, stars: repo.stargazers_count, url: repo.html_url, language: repo.language, updated: repo.updated_at })) };
  }

  if (action === "create_issue") {
    const { repo, title, body, labels } = params;
    if (!repo || !title) return { success: false, error: "repo and title required" };
    const r = await fetch(`${base}/repos/${repo}/issues`, {
      method: "POST", headers: h, body: JSON.stringify({ title, body: body || "", labels: labels || [] }),
    });
    const data = await r.json();
    return { success: !!data.id, connector: "github", action, issue_number: data.number, url: data.html_url, title: data.title };
  }

  if (action === "list_issues") {
    const { repo, state } = params;
    if (!repo) return { success: false, error: "repo required (e.g. username/repo-name)" };
    const r = await fetch(`${base}/repos/${repo}/issues?state=${state || "open"}&per_page=${params.limit || 10}`, { headers: h });
    const data = await r.json();
    return { success: true, connector: "github", action, issues: data.map((i: any) => ({ number: i.number, title: i.title, state: i.state, url: i.html_url, created: i.created_at })) };
  }

  if (action === "get_repo") {
    const { repo } = params;
    if (!repo) return { success: false, error: "repo required" };
    const r = await fetch(`${base}/repos/${repo}`, { headers: h });
    const data = await r.json();
    return { success: !data.message, connector: "github", action, name: data.full_name, description: data.description, stars: data.stargazers_count, forks: data.forks_count, language: data.language, url: data.html_url, default_branch: data.default_branch };
  }

  if (action === "create_file") {
    const { repo, path, content, message, branch } = params;
    if (!repo || !path || !content) return { success: false, error: "repo, path, content required" };
    const encoded = btoa(unescape(encodeURIComponent(content)));
    const r = await fetch(`${base}/repos/${repo}/contents/${path}`, {
      method: "PUT", headers: h,
      body: JSON.stringify({ message: message || `Add ${path} via STEW`, content: encoded, branch: branch || "main" }),
    });
    const data = await r.json();
    return { success: !!data.content, connector: "github", action, path: data.content?.path, url: data.content?.html_url };
  }

  return { success: false, error: `GitHub action '${action}' not supported. Use: list_repos, get_repo, create_issue, list_issues, create_file` };
}

async function handleGoogleSheets(action: string, params: any, token: string) {
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  if (action === "read_sheet") {
    const { spreadsheet_id, range } = params;
    if (!spreadsheet_id) return { success: false, error: "spreadsheet_id required" };
    const r = await fetch(`https://sheets.googleapis.com/v4/spreadsheets/${spreadsheet_id}/values/${range || "Sheet1"}`, { headers: h });
    const data = await r.json();
    return { success: !data.error, connector: "googlesheets", action, values: data.values || [], range: data.range };
  }

  if (action === "write_sheet") {
    const { spreadsheet_id, range, values } = params;
    if (!spreadsheet_id || !values) return { success: false, error: "spreadsheet_id and values required" };
    const r = await fetch(`https://sheets.googleapis.com/v4/spreadsheets/${spreadsheet_id}/values/${range || "Sheet1"}:append?valueInputOption=USER_ENTERED`, {
      method: "POST", headers: h,
      body: JSON.stringify({ values: Array.isArray(values[0]) ? values : [values] }),
    });
    const data = await r.json();
    return { success: !data.error, connector: "googlesheets", action, updated_cells: data.updates?.updatedCells };
  }

  if (action === "create_spreadsheet") {
    const { title } = params;
    const r = await fetch("https://sheets.googleapis.com/v4/spreadsheets", {
      method: "POST", headers: h, body: JSON.stringify({ properties: { title: title || "STEW Generated Sheet" } }),
    });
    const data = await r.json();
    return { success: !!data.spreadsheetId, connector: "googlesheets", action, spreadsheet_id: data.spreadsheetId, url: data.spreadsheetUrl, title };
  }

  return { success: false, error: `Google Sheets action '${action}' not supported. Use: read_sheet, write_sheet, create_spreadsheet` };
}

async function handleGoogleDrive(action: string, params: any, token: string) {
  const h = { Authorization: `Bearer ${token}` };

  if (action === "list_files") {
    const q = params.query ? `name contains '${params.query}'` : "";
    const url = `https://www.googleapis.com/drive/v3/files?pageSize=${params.limit || 10}&fields=files(id,name,mimeType,webViewLink,modifiedTime)${q ? "&q=" + encodeURIComponent(q) : ""}`;
    const r = await fetch(url, { headers: h });
    const data = await r.json();
    return { success: !data.error, connector: "googledrive", action, files: data.files || [] };
  }

  if (action === "get_file") {
    const { file_id } = params;
    if (!file_id) return { success: false, error: "file_id required" };
    const r = await fetch(`https://www.googleapis.com/drive/v3/files/${file_id}?fields=id,name,mimeType,webViewLink,size,modifiedTime`, { headers: h });
    const data = await r.json();
    return { success: !data.error, connector: "googledrive", action, ...data };
  }

  return { success: false, error: `Google Drive action '${action}' not supported. Use: list_files, get_file` };
}

async function handleGoogleDocs(action: string, params: any, token: string) {
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  if (action === "create_doc") {
    const { title, content } = params;
    // Create document
    const r = await fetch("https://docs.googleapis.com/v1/documents", {
      method: "POST", headers: h, body: JSON.stringify({ title: title || "STEW Document" }),
    });
    const doc = await r.json();
    if (!doc.documentId) return { success: false, error: "Failed to create document" };

    // Insert content if provided
    if (content) {
      await fetch(`https://docs.googleapis.com/v1/documents/${doc.documentId}:batchUpdate`, {
        method: "POST", headers: h,
        body: JSON.stringify({ requests: [{ insertText: { location: { index: 1 }, text: content } }] }),
      });
    }
    return { success: true, connector: "googledocs", action, document_id: doc.documentId, url: `https://docs.google.com/document/d/${doc.documentId}/edit`, title };
  }

  if (action === "read_doc") {
    const { document_id } = params;
    if (!document_id) return { success: false, error: "document_id required" };
    const r = await fetch(`https://docs.googleapis.com/v1/documents/${document_id}`, { headers: h });
    const data = await r.json();
    // Extract plain text
    let text = "";
    try {
      data.body?.content?.forEach((el: any) => {
        el.paragraph?.elements?.forEach((pe: any) => {
          if (pe.textRun?.content) text += pe.textRun.content;
        });
      });
    } catch {}
    return { success: !data.error, connector: "googledocs", action, title: data.title, text: text.trim(), document_id };
  }

  return { success: false, error: `Google Docs action '${action}' not supported. Use: create_doc, read_doc` };
}

async function handleNotion(action: string, params: any, token: string) {
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Notion-Version": "2022-06-28" };

  if (action === "list_pages") {
    const r = await fetch("https://api.notion.com/v1/search", {
      method: "POST", headers: h, body: JSON.stringify({ filter: { property: "object", value: "page" }, page_size: params.limit || 10 }),
    });
    const data = await r.json();
    return { success: !data.status, connector: "notion", action, pages: (data.results || []).map((p: any) => ({ id: p.id, title: p.properties?.title?.title?.[0]?.plain_text || p.properties?.Name?.title?.[0]?.plain_text || "Untitled", url: p.url })) };
  }

  if (action === "create_page") {
    const { database_id, title, content } = params;
    if (!database_id || !title) return { success: false, error: "database_id and title required" };
    const r = await fetch("https://api.notion.com/v1/pages", {
      method: "POST", headers: h,
      body: JSON.stringify({
        parent: { database_id },
        properties: { Name: { title: [{ text: { content: title } }] } },
        children: content ? [{ object: "block", type: "paragraph", paragraph: { rich_text: [{ type: "text", text: { content } }] } }] : [],
      }),
    });
    const data = await r.json();
    return { success: !!data.id, connector: "notion", action, page_id: data.id, url: data.url, title };
  }

  return { success: false, error: `Notion action '${action}' not supported. Use: list_pages, create_page` };
}

async function handleAirtable(action: string, params: any, token: string) {
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const { base_id, table_name } = params;

  if (action === "list_records") {
    if (!base_id || !table_name) return { success: false, error: "base_id and table_name required" };
    const r = await fetch(`https://api.airtable.com/v0/${base_id}/${encodeURIComponent(table_name)}?maxRecords=${params.limit || 10}`, { headers: h });
    const data = await r.json();
    return { success: !data.error, connector: "airtable", action, records: data.records || [], total: (data.records || []).length };
  }

  if (action === "create_record") {
    if (!base_id || !table_name || !params.fields) return { success: false, error: "base_id, table_name and fields required" };
    const r = await fetch(`https://api.airtable.com/v0/${base_id}/${encodeURIComponent(table_name)}`, {
      method: "POST", headers: h, body: JSON.stringify({ fields: params.fields }),
    });
    const data = await r.json();
    return { success: !!data.id, connector: "airtable", action, record_id: data.id, fields: data.fields };
  }

  return { success: false, error: `Airtable action '${action}' not supported. Use: list_records, create_record` };
}

async function handleDiscord(action: string, params: any, token: string) {
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  if (action === "list_servers") {
    const r = await fetch("https://discord.com/api/users/@me/guilds", { headers: h });
    const data = await r.json();
    return { success: Array.isArray(data), connector: "discord", action, servers: (Array.isArray(data) ? data : []).map((g: any) => ({ id: g.id, name: g.name, owner: g.owner })) };
  }

  if (action === "send_message") {
    const { channel_id, message } = params;
    if (!channel_id || !message) return { success: false, error: "channel_id and message required" };
    const r = await fetch(`https://discord.com/api/channels/${channel_id}/messages`, {
      method: "POST", headers: h, body: JSON.stringify({ content: message }),
    });
    const data = await r.json();
    return { success: !!data.id, connector: "discord", action, message_id: data.id, channel_id };
  }

  return { success: false, error: `Discord action '${action}' not supported. Use: list_servers, send_message` };
}

// ── MAIN SERVER ───────────────────────────────────────────────

Deno.serve(async (req) => {
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };

  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });

  const base44 = createClientFromRequest(req);
  let body: any = {};
  try { body = await req.json(); } catch {}

  const { api_key, connector, action, token, ...params } = body;

  // ── LIST available connectors (no auth needed) ──
  if (!connector || connector === "list") {
    return new Response(JSON.stringify({
      success: true,
      available_connectors: [
        { id: "gmail",           name: "Gmail",           actions: ["list_emails","send_email","read_email"],           plan: "starter+" },
        { id: "github",          name: "GitHub",          actions: ["list_repos","get_repo","create_issue","list_issues","create_file"], plan: "free+" },
        { id: "googlesheets",    name: "Google Sheets",   actions: ["read_sheet","write_sheet","create_spreadsheet"],   plan: "starter+" },
        { id: "googledrive",     name: "Google Drive",    actions: ["list_files","get_file"],                          plan: "starter+" },
        { id: "googledocs",      name: "Google Docs",     actions: ["create_doc","read_doc"],                          plan: "starter+" },
        { id: "googlecalendar",  name: "Google Calendar", actions: ["list_events","create_event"],                     plan: "pro+" },
        { id: "notion",          name: "Notion",          actions: ["list_pages","create_page"],                       plan: "pro+" },
        { id: "airtable",        name: "Airtable",        actions: ["list_records","create_record"],                   plan: "starter+" },
        { id: "discord",         name: "Discord",         actions: ["list_servers","send_message"],                    plan: "starter+" },
        { id: "slack",           name: "Slack",           actions: ["list_channels","send_message"],                   plan: "pro+" },
        { id: "linear",          name: "Linear",          actions: ["list_issues","create_issue"],                     plan: "pro+" },
        { id: "hubspot",         name: "HubSpot",         actions: ["list_contacts","create_contact"],                 plan: "pro+" },
      ],
      usage: {
        endpoint: "POST /stewConnectors",
        body: { api_key: "your_stew_api_key", connector: "gmail", action: "send_email", to: "...", subject: "...", body: "..." },
        note: "connector_token is required for most connectors. Generate it from your STEW dashboard.",
      },
    }), { status: 200, headers });
  }

  // ── Validate STEW API key ──
  if (!api_key) {
    return new Response(JSON.stringify({ success: false, error: "api_key required. Get yours at https://stew-agent.onrender.com" }), { status: 401, headers });
  }

  let dev: any = null;
  try {
    const devs = await base44.asServiceRole.entities.StewDeveloper.filter({ api_key });
    if (devs.length === 0) return new Response(JSON.stringify({ success: false, error: "Invalid API key" }), { status: 401, headers });
    dev = devs[0];
  } catch (e: any) {
    return new Response(JSON.stringify({ success: false, error: "Auth error: " + e.message }), { status: 500, headers });
  }

  // ── Check plan access ──
  if (!canAccessConnector(dev.plan || "free", connector)) {
    return new Response(JSON.stringify({
      success: false,
      error: `The '${connector}' connector requires a higher plan. Your plan: ${dev.plan || "free"}. Upgrade at https://stew-agent.onrender.com`,
      connector, your_plan: dev.plan || "free",
    }), { status: 403, headers });
  }

  // ── Check API limits ──
  const used = dev.api_calls_used || 0;
  const limit = dev.api_calls_limit || 100;
  if (used >= limit) {
    return new Response(JSON.stringify({ success: false, error: `API limit reached (${used}/${limit}). Upgrade at https://stew-agent.onrender.com` }), { status: 429, headers });
  }

  // ── OAuth token required for most connectors ──
  if (!token) {
    return new Response(JSON.stringify({
      success: false,
      error: `connector_token required for '${connector}'. Get it from your STEW dashboard or the /auth/connector endpoint.`,
      hint: "POST { api_key, connector, action, token: '<oauth_token>' }",
    }), { status: 400, headers });
  }

  // ── Route to correct connector handler ──
  let result: any;
  const start = Date.now();

  try {
    switch (connector) {
      case "gmail":          result = await handleGmail(action, params, token);         break;
      case "github":         result = await handleGitHub(action, params, token);        break;
      case "googlesheets":   result = await handleGoogleSheets(action, params, token);  break;
      case "googledrive":    result = await handleGoogleDrive(action, params, token);   break;
      case "googledocs":     result = await handleGoogleDocs(action, params, token);    break;
      case "notion":         result = await handleNotion(action, params, token);        break;
      case "airtable":       result = await handleAirtable(action, params, token);      break;
      case "discord":        result = await handleDiscord(action, params, token);       break;
      default:
        result = { success: false, error: `Connector '${connector}' coming soon. Available: gmail, github, googlesheets, googledrive, googledocs, notion, airtable, discord` };
    }
  } catch (e: any) {
    result = { success: false, error: `Connector error: ${e.message}` };
  }

  // ── Update usage counter ──
  await base44.asServiceRole.entities.StewDeveloper.update(dev.id, {
    api_calls_used: used + 1,
    last_active: new Date().toISOString(),
  }).catch(() => {});

  // ── Log the call ──
  await base44.asServiceRole.entities.StewAPILog.create({
    developer_id: dev.id,
    api_key: api_key.slice(0, 20) + "...",
    endpoint: `connector/${connector}/${action}`,
    method: "POST",
    status_code: result.success ? 200 : 400,
    response_time_ms: Date.now() - start,
    ip_address: req.headers.get("x-forwarded-for") || "unknown",
    user_agent: req.headers.get("user-agent") || "api",
  }).catch(() => {});

  return new Response(JSON.stringify({
    ...result,
    _stew_meta: {
      connector, action,
      calls_used: used + 1,
      calls_remaining: limit - used - 1,
      plan: dev.plan,
      response_time_ms: Date.now() - start,
    },
  }), { status: result.success ? 200 : 400, headers });
});
