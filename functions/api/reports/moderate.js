// Token-gated moderation for field reports.
//   GET  /api/reports/moderate?key=<MODERATION_TOKEN>             pending list
//   POST /api/reports/moderate?key=<MODERATION_TOKEN> {id, action} approve | reject
import { jsonResponse, modGate, listPending, applyModeration } from "./_lib.js";

function deny(d) {
  return d === "unconfigured"
    ? jsonResponse({ ok: false, error: "moderation token not configured" }, { status: 503 })
    : jsonResponse({ ok: false, error: "denied" }, { status: 401 });
}

export async function onRequest({ request, env }) {
  const d = modGate(request, env);
  if (d) return deny(d);
  if (!env.REPORTS_KV) return jsonResponse({ ok: false, error: "not enabled" }, { status: 503 });
  if (request.method === "GET") return jsonResponse({ ok: true, pending: await listPending(env) });
  if (request.method === "POST") {
    let body;
    try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "bad json" }, { status: 400 }); }
    const id = String(body.id || ""), action = String(body.action || "");
    if (!id || !["approve", "reject"].includes(action)) return jsonResponse({ ok: false, error: "bad request" }, { status: 400 });
    const r = await applyModeration(env, id, action);
    return jsonResponse(r, { status: r.ok ? 200 : (r.status || 400) });
  }
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, POST" } });
}
