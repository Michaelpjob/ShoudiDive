// POST /api/reports/submit: a field report (catch, sighting, bait, dive) into
// the pending queue. Rate limited per IP and per device; email required so a
// moderator can follow up and reputation can accrue; email never shown.
import {
  jsonResponse, validate, clip, emailOk, clientIp, sha256hex, rateOk, recordEmail, PENDING_PREFIX, MAX_AGE_DAYS,
} from "./_lib.js";

export async function onRequestPost({ request, env }) {
  if (!env || !env.REPORTS_KV) return jsonResponse({ ok: false, error: "reports are not enabled yet" }, { status: 503 });
  let body;
  try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "bad json" }, { status: 400 }); }
  const nowMs = Date.now();
  const v = validate(body || {}, nowMs);
  if (v.error) return jsonResponse({ ok: false, error: v.error }, { status: 422 });
  const email = clip(body.email, 120);
  if (!emailOk(email)) return jsonResponse({ ok: false, error: "a valid email is required (never shown)" }, { status: 422 });
  const name = clip(body.name, 60);
  const deviceId = String(body.deviceId || "").slice(0, 64) || "anon";
  const ipHash = (await sha256hex((clientIp(request) || "noip") + "|obs")).slice(0, 16);
  if (!(await rateOk(env, `ip:${ipHash}`, 8, 3600))) return jsonResponse({ ok: false, error: "rate limited" }, { status: 429 });
  if (!(await rateOk(env, `dev:${deviceId}`, 20, 86400))) return jsonResponse({ ok: false, error: "rate limited" }, { status: 429 });
  const id = crypto.randomUUID();
  const rec = { id, ...v.rec, name, email, deviceId, ipHash, submittedAt: nowMs, status: "pending" };
  const expiresAt = Date.parse(rec.date + "T12:00:00Z") + MAX_AGE_DAYS * 86400000;
  const ttl = Math.max(3600, Math.floor((expiresAt - nowMs) / 1000));
  await env.REPORTS_KV.put(PENDING_PREFIX + id, JSON.stringify(rec), { expirationTtl: ttl });
  await recordEmail(env, email, name, rec.type, nowMs);
  return jsonResponse({ ok: true, id, status: "pending" });
}

export async function onRequest({ request, env }) {
  if (request.method === "POST") return onRequestPost({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "POST" } });
}
