// Token-gated moderation API (reused by the admin review page).
//   GET  /api/paddies/moderate?key=<MODERATION_TOKEN>            — list pending reports
//   POST /api/paddies/moderate?key=<MODERATION_TOKEN> {id,action} — approve | reject
import { jsonResponse, modGate, listPending, applyModeration } from "./_lib.js";

function denyResponse(denied) {
  if (denied === "unconfigured")
    return jsonResponse({ ok: false, error: "moderation token not configured" }, { status: 503 });
  return jsonResponse({ ok: false, error: "denied" }, { status: 401 });
}

export async function onRequestGet({ request, env }) {
  const denied = modGate(request, env);
  if (denied) return denyResponse(denied);
  if (!env.REPORTS_KV) return jsonResponse({ ok: false, error: "not enabled" }, { status: 503 });
  return jsonResponse({ ok: true, pending: await listPending(env) });
}

export async function onRequestPost({ request, env }) {
  const denied = modGate(request, env);
  if (denied) return denyResponse(denied);
  if (!env.REPORTS_KV) return jsonResponse({ ok: false, error: "not enabled" }, { status: 503 });

  let body;
  try { body = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "bad json" }, { status: 400 }); }
  const id = String(body.id || "");
  const action = String(body.action || "");
  if (!id || !["approve", "reject"].includes(action))
    return jsonResponse({ ok: false, error: "bad request" }, { status: 400 });

  const r = await applyModeration(env, id, action);
  return jsonResponse(r, { status: r.ok ? 200 : (r.status || 400) });
}

export async function onRequest({ request, env }) {
  const m = request.method;
  if (m === "GET") return onRequestGet({ request, env });
  if (m === "POST") return onRequestPost({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, POST" } });
}
