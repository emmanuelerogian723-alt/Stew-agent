import { createClientFromRequest } from 'npm:@base44/sdk@0.8.31';

Deno.serve(async (req) => {
  const headers = { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" };
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });

  const base44 = createClientFromRequest(req);
  let body: any = {};
  try { body = await req.json(); } catch {}
  const { action, api_key, task, ...params } = body;

  if (!api_key)
    return new Response(JSON.stringify({ success: false, error: "API key required. Get yours at https://stew-agent.onrender.com" }), { status: 401, headers });

  try {
    const devs = await base44.asServiceRole.entities.StewDeveloper.filter({ api_key });
    if (devs.length === 0)
      return new Response(JSON.stringify({ success: false, error: "Invalid API key" }), { status: 401, headers });

    const dev = devs[0];

    if (dev.plan !== "free" && dev.plan_expires && new Date(dev.plan_expires) < new Date()) {
      await base44.asServiceRole.entities.StewDeveloper.update(dev.id, { payment_status: "expired" });
      return new Response(JSON.stringify({
        success: false,
        error: "Your plan has expired. Please renew to continue.",
        plan: dev.plan, expired_at: dev.plan_expires,
      }), { status: 402, headers });
    }

    const used = dev.api_calls_used || 0;
    const limit = dev.api_calls_limit || 100;

    if (used >= limit) {
      return new Response(JSON.stringify({
        success: false,
        error: `API limit reached (${used}/${limit}). Upgrade your plan to continue.`,
        calls_used: used, calls_limit: limit,
      }), { status: 429, headers });
    }

    const start = Date.now();
    let result: any = { success: false, error: "Unknown endpoint" };

    const stewUrl = "https://stew-agent.onrender.com";
    let stewEndpoint = "/task";
    let stewBody: any = { task: task || params.message || params.query || "Hello" };

    if (action === "chat")      { stewEndpoint = "/chat";           stewBody = { message: task }; }
    if (action === "search")    { stewEndpoint = "/search";         stewBody = { query: task, num_results: params.num_results || 5 }; }
    if (action === "research")  { stewEndpoint = "/research/deep";  stewBody = { topic: task }; }
    if (action === "code")      { stewEndpoint = "/code";           stewBody = { description: task, language: params.language || "python" }; }
    if (action === "weather")   { stewEndpoint = "/weather";        stewBody = { city: task }; }
    if (action === "scrape")    { stewEndpoint = "/scrape";         stewBody = { url: task }; }
    if (action === "translate") { stewEndpoint = "/translate";      stewBody = { text: task, target_language: params.target_language || "fr" }; }
    if (action === "summarize") { stewEndpoint = "/summarize";      stewBody = { text: task }; }
    if (action === "sentiment") { stewEndpoint = "/sentiment";      stewBody = { text: task }; }
    if (action === "pdf")       { stewEndpoint = "/build/pdf";      stewBody = { title: params.title || "Document", content: task }; }
    if (action === "browse")    { stewEndpoint = "/browse/navigate"; stewBody = { url: task, question: params.question }; }

    try {
      const resp = await fetch(`${stewUrl}${stewEndpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(stewBody),
      });
      result = await resp.json();
    } catch (e: any) {
      result = { success: false, error: `STEW error: ${e.message}` };
    }

    const elapsed = Date.now() - start;

    await base44.asServiceRole.entities.StewDeveloper.update(dev.id, {
      api_calls_used: used + 1,
      last_active: new Date().toISOString(),
    });

    await base44.asServiceRole.entities.StewAPILog.create({
      developer_id: dev.id,
      api_key: api_key.slice(0, 20) + "...",
      endpoint: action || "task",
      method: "POST",
      status_code: result.success ? 200 : 400,
      response_time_ms: elapsed,
      ip_address: req.headers.get("x-forwarded-for") || "unknown",
      user_agent: req.headers.get("user-agent") || "unknown",
    }).catch(() => {});

    return new Response(JSON.stringify({
      ...result,
      _stew_meta: {
        calls_used: used + 1,
        calls_remaining: limit - used - 1,
        plan: dev.plan,
        response_time_ms: elapsed,
      }
    }), { status: 200, headers });

  } catch (e: any) {
    return new Response(JSON.stringify({ success: false, error: e.message }), { status: 500, headers });
  }
});
