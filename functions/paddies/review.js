// GET/POST /paddies/review?key=<MODERATION_TOKEN>
// Server-rendered moderation queue. No client JS (POST-form buttons) so it
// stays clean under the site CSP (script-src 'self'). Mirrors /stats gating.
import { modGate, listPending, applyModeration, listEmails } from "../api/paddies/_lib.js";

const CSS = `
  :root{color-scheme:dark}
  body{margin:0;background:#0b1220;color:#e2e8f0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:760px;margin:0 auto;padding:18px 16px 60px}
  h1{font-size:18px;margin:0 0 2px}
  .sub{color:#94a3b8;font-size:12px;margin:0 0 16px}
  .empty{color:#94a3b8;padding:24px 0}
  .row{display:flex;align-items:center;gap:10px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:10px 12px;margin:8px 0}
  .meta{flex:1;min-width:0}
  .sp{font-weight:700;color:#fca5a5;text-transform:capitalize}
  .mut{color:#94a3b8;font-size:12px}
  a{color:#38bdf8}
  button{border:none;border-radius:7px;padding:7px 13px;font:600 13px system-ui;cursor:pointer;color:#fff}
  .ok{background:#16a34a}
  .no{background:#b91c1c}
  form{display:flex;align-items:center;gap:8px;margin:0}
  .rep{color:#cbd5e1;font-size:12px;margin-top:3px}
  .rep .em{color:#7dd3fc}
  .rep .nt{color:#fcd34d;font-style:italic}
  .nav{margin:0 0 14px;font-size:13px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #1e293b}
  th{color:#94a3b8;font-weight:600}
  td.em{color:#7dd3fc}
`;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function ago(ms) {
  const s = (Date.now() - ms) / 1000;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}
function htmlResponse(body, status = 200) {
  return new Response(`<!doctype html><html lang=en><head><meta charset=utf-8>` +
    `<meta name=viewport content="width=device-width,initial-scale=1">` +
    `<title>Paddy reports — review</title><style>${CSS}</style></head><body>${body}</body></html>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

function page(pending, key, flash) {
  const k = esc(key);
  const rows = pending.length ? pending.map((r) => `
    <div class=row>
      <div class=meta>
        <span class=sp>${esc(r.species)}</span>
        <span class=mut>&middot; ${r.lat}, ${r.lng}
          &middot; <a href="https://www.google.com/maps?q=${r.lat},${r.lng}" target=_blank rel=noopener>map</a>
          &middot; ${esc(r.date)} &middot; ${ago(r.submittedAt)} ago</span>
        <div class=rep>${esc(r.name || "Anonymous")} &middot; <span class=em>${esc(r.email || "—")}</span>${r.notes ? ` &middot; <span class=nt>&ldquo;${esc(r.notes)}&rdquo;</span>` : ""}</div>
      </div>
      <form method=POST>
        <input type=hidden name=key value="${k}">
        <input type=hidden name=id value="${esc(r.id)}">
        <button class=ok name=action value=approve>Approve</button>
        <button class=no name=action value=reject>Reject</button>
      </form>
    </div>`).join("") : `<div class=empty>No reports waiting. 🎉</div>`;
  return `<div class=wrap><h1>Paddy reports — review</h1>` +
    `<p class=sub>${pending.length} waiting${flash ? ` &middot; ${esc(flash)}` : ""}</p>` +
    `<div class=nav><a href="?key=${k}&amp;view=log">Reporter email log &rarr;</a></div>${rows}</div>`;
}

function emailLogPage(emails, key) {
  const k = esc(key);
  const rows = emails.length ? emails.map((e) => `
    <tr><td>${esc(e.name || "—")}</td><td class=em>${esc(e.email)}</td>
      <td>${e.count || 1}</td><td style="text-transform:capitalize">${esc(e.lastSpecies || "")}</td><td>${esc(e.lastDate || "")}</td></tr>`).join("")
    : `<tr><td colspan=5 class=mut>No emails collected yet.</td></tr>`;
  return `<div class=wrap><h1>Reporter email log</h1>` +
    `<p class=sub>${emails.length} unique reporter${emails.length === 1 ? "" : "s"} &middot; saved permanently (survives report expiry)</p>` +
    `<div class=nav><a href="?key=${k}">&larr; back to review queue</a></div>` +
    `<table><tr><th>Name</th><th>Email</th><th>Reports</th><th>Last species</th><th>Last date</th></tr>${rows}</table></div>`;
}

function denied(reason) {
  if (reason === "unconfigured") return htmlResponse(`<div class=wrap><h1>Review</h1><p class=sub>Moderation token isn't configured on the server.</p></div>`, 503);
  return htmlResponse(`<div class=wrap><h1>Review</h1><p class=sub>Access denied. Append <code>?key=YOUR_TOKEN</code> to the URL.</p></div>`, 401);
}

export async function onRequestGet({ request, env }) {
  const d = modGate(request, env);
  if (d) return denied(d);
  if (!env.REPORTS_KV) return htmlResponse(`<div class=wrap><h1>Review</h1><p class=sub>Reports backend not enabled.</p></div>`, 503);
  const url = new URL(request.url);
  const key = url.searchParams.get("key");
  if (url.searchParams.get("view") === "log") return htmlResponse(emailLogPage(await listEmails(env), key));
  return htmlResponse(page(await listPending(env), key));
}

export async function onRequestPost({ request, env }) {
  if (!env.REPORTS_KV) return denied("unconfigured");
  const form = await request.formData();
  const key = form.get("key") || "";
  const expected = env.MODERATION_TOKEN;
  if (!expected) return denied("unconfigured");
  if (key.length !== expected.length || key !== expected) return denied("denied");

  const id = String(form.get("id") || "");
  const action = String(form.get("action") || "");
  let flash = "";
  if (id && (action === "approve" || action === "reject")) {
    const r = await applyModeration(env, id, action);
    flash = r.ok ? (action === "approve" ? "approved" : "rejected") : `error: ${r.error}`;
  }
  return htmlResponse(page(await listPending(env), key, flash));
}

export async function onRequest({ request, env }) {
  const m = request.method;
  if (m === "GET") return onRequestGet({ request, env });
  if (m === "POST") return onRequestPost({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, POST" } });
}
