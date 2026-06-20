// POST /api/paddies/report   — submit a catch report (lands in the pending queue)
// DELETE /api/paddies/report?id=<id> — remove a report (owner via x-device-id, or admin via ?key=)
import {
  jsonResponse, bboxOk, snap, normSpecies, dateOk, clientIp, sha256hex,
  rateOk, readApproved, writeApproved, recordEmail, clip, emailOk,
  PENDING_PREFIX, MAX_AGE_DAYS,
} from "./_lib.js";

export async function onRequestPost({ request, env }) {
  if (!env || !env.REPORTS_KV) {
    return jsonResponse({ ok: false, error: "submissions not enabled yet" }, { status: 503 });
  }
  let body;
  try { body = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "bad json" }, { status: 400 }); }

  const lat0 = Number(body.lat), lng0 = Number(body.lng);
  if (!bboxOk(lat0, lng0)) return jsonResponse({ ok: false, error: "out of area" }, { status: 422 });
  const species = normSpecies(body.species);
  if (!species) return jsonResponse({ ok: false, error: "unknown species" }, { status: 422 });
  const nowMs = Date.now();
  const date = String(body.date || "").slice(0, 10);
  if (!dateOk(date, nowMs)) return jsonResponse({ ok: false, error: "date out of range" }, { status: 422 });

  const email = clip(body.email, 120);
  if (!emailOk(email)) return jsonResponse({ ok: false, error: "valid email required" }, { status: 422 });
  const name = clip(body.name, 60);     // optional reporter name
  const notes = clip(body.notes, 280);  // optional free-text notes

  const deviceId = (String(body.deviceId || "").slice(0, 64)) || "anon";
  const ipHash = (await sha256hex((clientIp(request) || "noip") + "|paddies")).slice(0, 16);

  // Spam guard: cap per IP (5/hr) and per device (10/day).
  if (!(await rateOk(env, `ip:${ipHash}`, 5, 3600)))
    return jsonResponse({ ok: false, error: "rate limited" }, { status: 429 });
  if (!(await rateOk(env, `dev:${deviceId}`, 10, 86400)))
    return jsonResponse({ ok: false, error: "rate limited" }, { status: 429 });

  const id = crypto.randomUUID();
  const rec = {
    id,
    lat: snap(lat0), lng: snap(lng0),   // coarse-snapped before it's ever stored
    species, date,
    name, email, notes,                 // PII: moderator-only, never in publicView
    deviceId, ipHash,
    submittedAt: nowMs,
    expiresAt: nowMs + MAX_AGE_DAYS * 86400000,
    status: "pending",
  };
  // Pending key auto-expires if it's never moderated, so the queue can't rot forever.
  await env.REPORTS_KV.put(PENDING_PREFIX + id, JSON.stringify(rec), { expirationTtl: MAX_AGE_DAYS * 86400 });
  // Durable email log for the moderator (survives report expiry / rejection).
  await recordEmail(env, email, name, species, date, nowMs);
  return jsonResponse({ ok: true, id, status: "pending" });
}

export async function onRequestDelete({ request, env }) {
  if (!env || !env.REPORTS_KV) return jsonResponse({ ok: false, error: "not enabled" }, { status: 503 });
  const url = new URL(request.url);
  const id = url.searchParams.get("id") || "";
  if (!id) return jsonResponse({ ok: false, error: "missing id" }, { status: 400 });

  const device = request.headers.get("x-device-id") || "";
  const tok = url.searchParams.get("key") || request.headers.get("x-mod-token") || "";
  const isAdmin = !!(env.MODERATION_TOKEN && tok.length === env.MODERATION_TOKEN.length && tok === env.MODERATION_TOKEN);

  // Pending first.
  const pkey = PENDING_PREFIX + id;
  const praw = await env.REPORTS_KV.get(pkey);
  if (praw) {
    const rec = JSON.parse(praw);
    if (isAdmin || (device && device === rec.deviceId)) {
      await env.REPORTS_KV.delete(pkey);
      return jsonResponse({ ok: true, removed: "pending" });
    }
    return jsonResponse({ ok: false, error: "forbidden" }, { status: 403 });
  }
  // Otherwise it may be in the approved blob.
  const nowMs = Date.now();
  const arr = await readApproved(env, nowMs);
  const target = arr.find((r) => r.id === id);
  if (!target) return jsonResponse({ ok: false, error: "not found" }, { status: 404 });
  if (!(isAdmin || (device && device === target.deviceId)))
    return jsonResponse({ ok: false, error: "forbidden" }, { status: 403 });
  await writeApproved(env, arr.filter((r) => r.id !== id));
  return jsonResponse({ ok: true, removed: "approved" });
}

export async function onRequest({ request, env }) {
  const m = request.method;
  if (m === "POST") return onRequestPost({ request, env });
  if (m === "DELETE") return onRequestDelete({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "POST, DELETE" } });
}
