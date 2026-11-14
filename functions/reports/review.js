// GET/POST /reports/review?key=<MODERATION_TOKEN>
// Server-rendered review queue for Ocean Report field reports. No client JS
// (POST-form buttons) so it stays inside the site CSP. Mirrors /paddies/review.
import { modGate, listPending, applyModeration, listEmails, repScore } from "../api/reports/_lib.js";

const CSS = `
  :root{color-scheme:dark}
  body{margin:0;background:#0b1220;color:#e2e8f0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:780px;margin:0 auto;padding:18px 16px 60px}
  h1{font-size:18px;margin:0 0 2px}
  .sub{color:#94a3b8;font-size:12px;margin:0 0 16px}
  .empty{color:#94a3b8;padding:24px 0}
  .row{display:flex;align-items:flex-start;gap:10px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:10px 12px;margin:8px 0}
  .meta{flex:1;min-width:0}
  .ty{font-weight:700;color:#7dd3fc;text-transform:capitalize}
  .mut{color:#94a3b8;font-size:12px}
  .nt{color:#fcd34d;font-style:italic}
  a{color:#38bdf8}
  button{border:none;border-radius:7px;padding:7px 13px;font:600 13px system-ui;cursor:pointer;color:#fff}
  .ok{background:#16a34a}.no{background:#b91c1c}
  form{display:flex;gap:8px;margin:0}
`;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function html(body, status = 200) {
  return new Response(`<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">` +
    `<title>Field reports: review</title><style>${CSS}</style></head><body>${body}</body></html>`,
  { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
}
function what(r) {
  if (r.type === "catch") return (r.species || []).join(", ") + (r.bait ? ` on ${r.bait}` : "");
  if (r.type === "sighting") return r.sighting + ((r.species || []).length ? ` (${r.species.join(", ")})` : "");
  if (r.type === "bait") return `${r.barge}: ${r.bait}${r.bait_size ? " " + r.bait_size : ""}`;
  if (r.type === "dive") return `vis ${r.vis_lo_ft}-${r.vis_hi_ft} ft` + (r.depth_ft ? ` at ${r.depth_ft} ft` : "") + (r.surge ? `, ${r.surge} surge` : "");
  return "";
}
function page(pending, key, flash, reps) {
  const k = esc(key);
  const rows = pending.length ? pending.map((r) => {
    const er = reps[(r.email || "").toLowerCase()];
    const seen = er ? (er.approved || 0) + (er.rejected || 0) : 0;
    return `<div class=row><div class=meta><span class=ty>${esc(r.type)}</span> &middot; ${esc(what(r))}
      <div class=mut>${esc(r.date)} &middot; ${r.lat}, ${r.lng} &middot; <a href="https://www.google.com/maps?q=${r.lat},${r.lng}" target=_blank rel="noopener noreferrer">map</a>
      ${r.water_temp_f ? ` &middot; ${esc(r.water_temp_f)}&deg;F` : ""}${r.color ? ` &middot; ${esc(r.color)} water` : ""}</div>
      <div class=mut>${esc(r.name || "Anonymous")} &middot; ${esc(r.email || "")} &middot; ${seen ? `rep ${repScore(er).toFixed(2)} (${er.approved || 0} approved, ${er.rejected || 0} rejected)` : "new reporter"}</div>
      ${r.notes ? `<div class=nt>&ldquo;${esc(r.notes)}&rdquo;</div>` : ""}</div>
      <form method=POST><input type=hidden name=key value="${k}"><input type=hidden name=id value="${esc(r.id)}">
      <button class=ok name=action value=approve>Approve</button><button class=no name=action value=reject>Reject</button></form></div>`;
  }).join("") : `<div class=empty>No field reports waiting.</div>`;
  return `<div class=wrap><h1>Field reports: review</h1><p class=sub>${pending.length} waiting${flash ? ` &middot; ${esc(flash)}` : ""}</p>${rows}</div>`;
}
function denied(d) {
  return d === "unconfigured"
    ? html(`<div class=wrap><h1>Review</h1><p class=sub>Moderation token isn't configured.</p></div>`, 503)
    : html(`<div class=wrap><h1>Review</h1><p class=sub>Access denied. Append <code>?key=YOUR_TOKEN</code>.</p></div>`, 401);
}
async function reps(env) {
  const m = {};
  for (const e of await listEmails(env)) if (e.email) m[e.email.toLowerCase()] = e;
  return m;
}

export async function onRequestGet({ request, env }) {
  const d = modGate(request, env);
  if (d) return denied(d);
  if (!env.REPORTS_KV) return html(`<div class=wrap><p class=sub>Reports backend not enabled.</p></div>`, 503);
  const key = new URL(request.url).searchParams.get("key");
  return html(page(await listPending(env), key, null, await reps(env)));
}

export async function onRequestPost({ request, env }) {
  if (!env.REPORTS_KV || !env.MODERATION_TOKEN) return denied("unconfigured");
  const form = await request.formData();
  const key = String(form.get("key") || "");
  if (key.length !== env.MODERATION_TOKEN.length || key !== env.MODERATION_TOKEN) return denied("denied");
  const id = String(form.get("id") || ""), action = String(form.get("action") || "");
  let flash = "";
  if (id && (action === "approve" || action === "reject")) {
    const r = await applyModeration(env, id, action);
    flash = r.ok ? (action === "approve" ? "approved" : "rejected") : `error: ${r.error}`;
  }
  return html(page(await listPending(env), key, flash, await reps(env)));
}

export async function onRequest({ request, env }) {
  if (request.method === "GET") return onRequestGet({ request, env });
  if (request.method === "POST") return onRequestPost({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, POST" } });
}
