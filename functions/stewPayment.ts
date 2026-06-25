import { createClientFromRequest } from 'npm:@base44/sdk@0.8.31';

const PAYSTACK_SECRET = Deno.env.get("PAYSTACK_SECRET_KEY") || "";
const PAYSTACK_PUBLIC = Deno.env.get("PAYSTACK_PUBLIC_KEY") || "pk_live_b73d27d70e64ebb36f0c2f2b79cb39f4ba5da3bf";

const PLANS: Record<string, { name: string; price_ngn: number; calls: number; badge: string; features: string[] }> = {
  free:       { name: "Free Starter",  price_ngn: 0,     calls: 1000,    badge: "Free Forever",    features: ["1,000 API calls/month", "All basic endpoints", "Chat, Search, Code, Translate", "API key dashboard"] },
  starter:    { name: "Starter Pro",   price_ngn: 5000,  calls: 10000,   badge: "Most Popular",    features: ["10,000 API calls/month", "All endpoints", "Browser automation", "Priority queue"] },
  pro:        { name: "Pro Builder",   price_ngn: 15000, calls: 100000,  badge: "Best Value",      features: ["100,000 API calls/month", "Vision & OCR", "100-Agent swarm", "Deep research engine", "Priority support"] },
  enterprise: { name: "Enterprise",    price_ngn: 49000, calls: 1000000, badge: "Unlimited Power", features: ["1,000,000 API calls/month", "Everything in Pro", "Dedicated compute", "Custom fine-tuning", "SLA guarantee", "WhatsApp support"] },
};

const PLAN_DURATION_DAYS = 30;

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
  const { action } = body;

  try {
    // ── GET PLANS ──
    if (action === "plans") {
      return new Response(JSON.stringify({
        success: true,
        plans: PLANS,
        currency: "NGN",
        gateway: "Paystack",
        public_key: PAYSTACK_PUBLIC,
      }), { status: 200, headers });
    }

    // ── CHECK PAYSTACK KEY ──
    if (action === "check_key") {
      const hasKey = PAYSTACK_SECRET.length > 10;
      return new Response(JSON.stringify({
        success: true,
        has_secret: hasKey,
        public_key: PAYSTACK_PUBLIC,
        gateway_status: hasKey ? "configured" : "needs_secret_key",
      }), { status: 200, headers });
    }

    // ── INITIALIZE PAYMENT ──
    if (action === "initialize") {
      const { email, plan, developer_id, full_name } = body;
      if (!email || !plan || !PLANS[plan])
        return new Response(JSON.stringify({ success: false, error: "Invalid plan or email" }), { status: 400, headers });
      if (plan === "free")
        return new Response(JSON.stringify({ success: false, error: "Free plan needs no payment" }), { status: 400, headers });

      const planData = PLANS[plan];
      const amount = planData.price_ngn * 100; // Paystack uses kobo
      const reference = `STEW_${plan.toUpperCase()}_${Date.now()}_${Math.random().toString(36).slice(2,8).toUpperCase()}`;

      // If no Paystack secret key, return mock success for testing
      if (!PAYSTACK_SECRET || PAYSTACK_SECRET.length < 10) {
        return new Response(JSON.stringify({
          success: true,
          mock: true,
          reference,
          authorization_url: `https://checkout.paystack.com/mock_${reference}`,
          access_code: `mock_${reference}`,
          plan: planData,
          amount_ngn: planData.price_ngn,
          message: "⚠️ Test mode: Add PAYSTACK_SECRET_KEY to environment variables for live payments",
          public_key: PAYSTACK_PUBLIC,
        }), { status: 200, headers });
      }

      const response = await fetch("https://api.paystack.co/transaction/initialize", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${PAYSTACK_SECRET}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          amount,
          currency: "NGN",
          reference,
          callback_url: "https://stew-agent.onrender.com/?payment=success",
          metadata: {
            developer_id: developer_id || "",
            plan,
            full_name: full_name || email,
            custom_fields: [
              { display_name: "Plan", variable_name: "plan", value: planData.name },
              { display_name: "API Calls", variable_name: "calls", value: planData.calls.toLocaleString() },
            ],
          },
          channels: ["card", "bank", "ussd", "bank_transfer", "mobile_money", "qr"],
        }),
      });

      const data = await response.json();
      if (!data.status)
        return new Response(JSON.stringify({ success: false, error: data.message || "Payment init failed" }), { status: 400, headers });

      return new Response(JSON.stringify({
        success: true,
        reference,
        authorization_url: data.data.authorization_url,
        access_code: data.data.access_code,
        plan: planData,
        amount_ngn: planData.price_ngn,
        public_key: PAYSTACK_PUBLIC,
      }), { status: 200, headers });
    }

    // ── VERIFY PAYMENT ──
    if (action === "verify") {
      const { reference, developer_id, plan } = body;
      if (!reference)
        return new Response(JSON.stringify({ success: false, error: "Reference required" }), { status: 400, headers });

      // Mock verification for testing (when no secret key)
      if (!PAYSTACK_SECRET || PAYSTACK_SECRET.length < 10) {
        const planData = PLANS[plan || "starter"];
        const expires = new Date();
        expires.setDate(expires.getDate() + PLAN_DURATION_DAYS);

        // Update developer in DB if ID provided
        if (developer_id) {
          try {
            await base44.asServiceRole.entities.StewDeveloper.update(developer_id, {
              plan: plan || "starter",
              api_calls_limit: planData.calls,
              api_calls_used: 0,
              payment_status: "active",
              paystack_reference: reference,
              plan_expires: expires.toISOString(),
              total_paid_ngn: planData.price_ngn,
            });
          } catch(e) { /* dev not in DB */ }
        }

        return new Response(JSON.stringify({
          success: true,
          mock: true,
          verified: true,
          plan: plan || "starter",
          calls_limit: planData.calls,
          expires: expires.toISOString(),
          amount_paid_ngn: planData.price_ngn,
          reference,
          message: "Mock verification successful (test mode)",
        }), { status: 200, headers });
      }

      const response = await fetch(`https://api.paystack.co/transaction/verify/${reference}`, {
        headers: { Authorization: `Bearer ${PAYSTACK_SECRET}` },
      });

      const data = await response.json();
      if (!data.status || data.data?.status !== "success")
        return new Response(JSON.stringify({
          success: false,
          error: "Payment not successful",
          paystack_status: data.data?.status,
          reference,
        }), { status: 400, headers });

      const metadata = data.data.metadata || {};
      const paidPlan = metadata.plan || plan || "starter";
      const planData = PLANS[paidPlan];
      const amountPaidNgn = Math.round(data.data.amount / 100);
      const expires = new Date();
      expires.setDate(expires.getDate() + PLAN_DURATION_DAYS);

      // Update developer record in DB
      const devId = developer_id || metadata.developer_id;
      if (devId) {
        try {
          await base44.asServiceRole.entities.StewDeveloper.update(devId, {
            plan: paidPlan,
            api_calls_limit: planData?.calls || 10000,
            api_calls_used: 0,
            payment_status: "active",
            paystack_reference: reference,
            plan_expires: expires.toISOString(),
            total_paid_ngn: amountPaidNgn,
          });
        } catch(e) { /* continue */ }
      }

      // Log API event
      try {
        await base44.asServiceRole.entities.StewAPILog.create({
          developer_id: devId || "unknown",
          api_key: "payment",
          endpoint: "/payment/verify",
          method: "POST",
          status_code: 200,
          response_time_ms: 0,
          ip_address: "paystack",
          user_agent: `plan:${paidPlan}|amount:${amountPaidNgn}NGN|ref:${reference}`,
        });
      } catch(e) { /* continue */ }

      return new Response(JSON.stringify({
        success: true,
        verified: true,
        plan: paidPlan,
        calls_limit: planData?.calls || 10000,
        expires: expires.toISOString(),
        amount_paid_ngn: amountPaidNgn,
        reference,
        channel: data.data.channel,
        message: `✅ Payment confirmed! ${planData?.name} plan activated.`,
      }), { status: 200, headers });
    }

    // ── GET DEVELOPER STATS ──
    if (action === "get_stats") {
      const { developer_id, api_key } = body;
      try {
        let devs;
        if (developer_id) {
          const dev = await base44.asServiceRole.entities.StewDeveloper.get(developer_id);
          devs = dev ? [dev] : [];
        } else if (api_key) {
          devs = await base44.asServiceRole.entities.StewDeveloper.filter({ api_key });
        } else {
          return new Response(JSON.stringify({ success: false, error: "developer_id or api_key required" }), { status: 400, headers });
        }

        if (devs.length === 0)
          return new Response(JSON.stringify({ success: false, error: "Developer not found" }), { status: 404, headers });

        const dev = devs[0];
        const pct = Math.round(((dev.api_calls_used || 0) / (dev.api_calls_limit || 1)) * 100);

        return new Response(JSON.stringify({
          success: true,
          developer: {
            id: dev.id,
            full_name: dev.full_name,
            email: dev.email,
            plan: dev.plan || "free",
            api_calls_used: dev.api_calls_used || 0,
            api_calls_limit: dev.api_calls_limit || 1000,
            usage_percent: pct,
            payment_status: dev.payment_status || "active",
            plan_expires: dev.plan_expires,
            total_paid_ngn: dev.total_paid_ngn || 0,
            api_key_preview: (dev.api_key || "").slice(0, 20) + "...",
          },
        }), { status: 200, headers });
      } catch(e) {
        return new Response(JSON.stringify({ success: false, error: "Could not fetch stats" }), { status: 500, headers });
      }
    }

    return new Response(JSON.stringify({ success: false, error: `Unknown action: ${action}. Use: plans, initialize, verify, get_stats, check_key` }), { status: 400, headers });

  } catch (error: any) {
    console.error("stewPayment error:", error);
    return new Response(JSON.stringify({ success: false, error: error.message || "Internal server error" }), { status: 500, headers });
  }
});
