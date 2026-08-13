import { createClientFromRequest } from 'npm:@base44/sdk@0.8.31';
import { crypto } from 'npm:@std/crypto@1.0.4';

function generateAPIKey(): string {
  const bytes = new Uint8Array(24);
  globalThis.crypto.getRandomValues(bytes);
  const hex = Array.from(bytes).map(b => b.toString(16).padStart(2,'0')).join('');
  return `stew_live_${hex}`;
}

async function hashPassword(password: string): Promise<string> {
  const data = new TextEncoder().encode(password + "MUTYINT_STEW_SALT_2026");
  const hashBuffer = await globalThis.crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2,'0')).join('');
}

const PLAN_LIMITS: Record<string, number> = {
  free: 100, starter: 5000, pro: 50000, enterprise: 1000000,
};

Deno.serve(async (req) => {
  const headers = { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" };
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });

  const base44 = createClientFromRequest(req);
  let body: any = {};
  try { body = await req.json(); } catch {}
  const { action } = body;

  try {
    // ── REGISTER ──
    if (action === "register") {
      const { full_name, email, password, company } = body;
      if (!full_name || !email || !password)
        return new Response(JSON.stringify({ success: false, error: "Name, email and password required" }), { status: 400, headers });

      const existing = await base44.asServiceRole.entities.StewDeveloper.filter({ email: email.toLowerCase().trim() });
      if (existing.length > 0)
        return new Response(JSON.stringify({ success: false, error: "Email already registered" }), { status: 409, headers });

      const dev = await base44.asServiceRole.entities.StewDeveloper.create({
        full_name,
        email: email.toLowerCase().trim(),
        company: company || "",
        password_hash: await hashPassword(password),
        plan: "free",
        api_key: generateAPIKey(),
        api_calls_used: 0,
        api_calls_limit: PLAN_LIMITS["free"],
        payment_status: "active",
        is_verified: true,
        verification_token: generateAPIKey().slice(10, 26),
        last_active: new Date().toISOString(),
        total_paid_ngn: 0,
      });

      return new Response(JSON.stringify({
        success: true,
        message: "Account created! Welcome to S.T.E.W API.",
        developer: {
          id: dev.id, full_name: dev.full_name, email: dev.email,
          plan: dev.plan, api_key: dev.api_key,
          api_calls_limit: dev.api_calls_limit, api_calls_used: 0,
        }
      }), { status: 200, headers });
    }

    // ── LOGIN ──
    if (action === "login") {
      const { email, password } = body;
      if (!email || !password)
        return new Response(JSON.stringify({ success: false, error: "Email and password required" }), { status: 400, headers });

      const devs = await base44.asServiceRole.entities.StewDeveloper.filter({ email: email.toLowerCase().trim() });
      if (devs.length === 0)
        return new Response(JSON.stringify({ success: false, error: "Invalid email or password" }), { status: 401, headers });

      const dev = devs[0];
      const hashed = await hashPassword(password);
      if (dev.password_hash !== hashed)
        return new Response(JSON.stringify({ success: false, error: "Invalid email or password" }), { status: 401, headers });

      await base44.asServiceRole.entities.StewDeveloper.update(dev.id, { last_active: new Date().toISOString() });
      const isPlanExpired = dev.plan !== "free" && dev.plan_expires && new Date(dev.plan_expires) < new Date();

      return new Response(JSON.stringify({
        success: true,
        developer: {
          id: dev.id, full_name: dev.full_name, email: dev.email, company: dev.company,
          plan: dev.plan, api_key: dev.api_key,
          api_calls_used: dev.api_calls_used || 0,
          api_calls_limit: dev.api_calls_limit || PLAN_LIMITS[dev.plan] || 100,
          payment_status: isPlanExpired ? "expired" : dev.payment_status,
          plan_expires: dev.plan_expires || null,
          total_paid_ngn: dev.total_paid_ngn || 0,
        }
      }), { status: 200, headers });
    }

    // ── DASHBOARD ──
    if (action === "dashboard") {
      const { api_key } = body;
      if (!api_key)
        return new Response(JSON.stringify({ success: false, error: "API key required" }), { status: 400, headers });

      const devs = await base44.asServiceRole.entities.StewDeveloper.filter({ api_key });
      if (devs.length === 0)
        return new Response(JSON.stringify({ success: false, error: "Invalid API key" }), { status: 401, headers });

      const dev = devs[0];
      const used = dev.api_calls_used || 0;
      const limit = dev.api_calls_limit || 100;
      const usage_pct = Math.round((used / limit) * 100);
      const isPlanExpired = dev.plan !== "free" && dev.plan_expires && new Date(dev.plan_expires) < new Date();

      return new Response(JSON.stringify({
        success: true,
        developer: {
          id: dev.id, full_name: dev.full_name, email: dev.email, company: dev.company,
          plan: dev.plan, api_key: dev.api_key,
          api_calls_used: used, api_calls_limit: limit, usage_percent: usage_pct,
          payment_status: isPlanExpired ? "expired" : dev.payment_status,
          plan_expires: dev.plan_expires, total_paid_ngn: dev.total_paid_ngn || 0,
        }
      }), { status: 200, headers });
    }

    // ── ROTATE KEY ──
    if (action === "rotate_key") {
      const { api_key } = body;
      const devs = await base44.asServiceRole.entities.StewDeveloper.filter({ api_key });
      if (devs.length === 0)
        return new Response(JSON.stringify({ success: false, error: "Invalid API key" }), { status: 401, headers });
      const newKey = generateAPIKey();
      await base44.asServiceRole.entities.StewDeveloper.update(devs[0].id, { api_key: newKey });
      return new Response(JSON.stringify({ success: true, new_api_key: newKey }), { status: 200, headers });
    }

    return new Response(JSON.stringify({ success: false, error: "Unknown action" }), { status: 400, headers });

  } catch (e: any) {
    return new Response(JSON.stringify({ success: false, error: e.message }), { status: 500, headers });
  }
});
