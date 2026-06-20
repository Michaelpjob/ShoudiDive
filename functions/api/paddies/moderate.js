// Token-gated moderation API (reused by the admin review page).
//   GET  /api/paddies/moderate?key=<MODERATION_TOKEN>            — list pending reports
//   POST /api/paddies/moderate?key=<MODERATION_TOKEN> {id,action} — approve | reject
import { jsonResponse, modGate, readApproved, writeApproved, PENDING_PREFIX } from "./_lib.js";

function denyResponse(denied) {
  if (denied === "unconfigured")
    return jsonResponse({ ok: false, error: "moderation token not configured" }, { status: 503 });
  return jsonResponse({ ok: false, error: "denied" }, { status: 401 });
}

export async function onRequestGet({ request, env }) {
  const denied = modGate(request, env);
  if (denied) return denyResponse(denied);
  if (!env.REPORTS_KV) return jsonResponse({ ok: false, error: "not enabled" }, { status: 503 });

  const out = [];
  let cursor;
  do {
    const res = await env.REPORTS_KV.list({ prefix: PENDING_PREFIX, cursor, limit: 1000 });
    for (const k of res.keys) {
      const raw = await env.REPORTS_KV.get(k.name);
      if (raw) { try { out.push(JSON.parse(raw)); } catch { /* skip */ } }
    }
    cursor = res.list_complete ? null : res.cursor;
  } while (cursor);
  out.sort((a, b) => b.submittedAt - a.submittedAt);
  return jsonResponse({ ok: true, pending: out });
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

  const pkey = PENDING_PREFIX + id;
  const raw = await env.REPORTS_KV.get(pkey);
  if (!raw) return jsonResponse({ ok: false, error: "not found" }, { status: 404 });
  const rec = JSON.parse(raw);
  await env.REPORTS_KV.delete(pkey);

  if (action === "reject") return jsonResponse({ ok: true, action: "reject", id });

  // approve → append to the approved blob (which the public GET serves)
  const nowMs = Date.now();
  const arr = await readApproved(env, nowMs);
  rec.status = "approved";
  rec.approvedAt = nowMs;
  arr.push(rec);
  await writeApproved(env, arr);
  return jsonResponse({ ok: true, action: "approve", id });
}

export async function onRequest({ request, env }) {
  const m = request.method;
  if (m === "GET") return onRequestGet({ request, env });
  if (m === "POST") return onRequestPost({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, POST" } });
}
