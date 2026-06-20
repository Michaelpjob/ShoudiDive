// Cloudflare Pages Function — analytics summary (JSON).
// Mounts at /api/analytics/summary. Token-gated (see _lib.gate).
//
// Reads ANALYTICS_KV and returns per-day + windowed totals for each event
// name. Counting is done from key names only (no value reads).
//
//   GET /api/analytics/summary?days=14&key=<STATS_TOKEN>
//   → { ok, generated_at, window_days, days:[{date,events}], totals }

import { gate, aggregate } from "./_lib.js";

function json(body, status) {
  return new Response(JSON.stringify(body, null, 2), {
    status: status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export async function onRequestGet({ request, env }) {
  const denied = gate(request, env);
  if (denied) {
    return json({ ok: false, error: denied }, denied === "unconfigured" ? 503 : 401);
  }
  if (!env || !env.ANALYTICS_KV) {
    return json({ ok: false, error: "kv-unbound" }, 503);
  }
  const url = new URL(request.url);
  let days = parseInt(url.searchParams.get("days") || "14", 10);
  if (!Number.isFinite(days) || days < 1) days = 14;
  days = Math.min(days, 90);

  const data = await aggregate(env, days);
  return json({ ok: true, generated_at: new Date().toISOString(), window_days: days, ...data });
}

export async function onRequest({ request, env }) {
  if (request.method === "GET") return onRequestGet({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
