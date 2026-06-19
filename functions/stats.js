// Cloudflare Pages Function — analytics dashboard (server-rendered HTML).
// Mounts at /stats. Token-gated (see _lib.gate).
//
//   GET /stats?days=14&key=<STATS_TOKEN>
//
// Server-rendered with zero client JS, so it's CSP-safe under the site's
// `script-src 'self'` policy (inline <style> is allowed by style-src
// 'unsafe-inline'). Reads the same aggregation as /api/analytics/summary.

import { gate, aggregate } from "./api/analytics/_lib.js";

const LABELS = {
  pageview: "Page views",
  whatsnew_open: "What's New opens",
  layer_change: "Layer changes",
  sst_mode_change: "SST mode toggles",
  spot_click: "Saved-spot clicks",
  settings_change: "Settings changes",
  tooltip_open: "Tooltip opens",
  popup_open: "Popup opens",
  timeline_drag: "Timeline drags",
  zoom: "Zooms",
  share_click: "Share clicks",
  tip_click: "Tip-jar clicks",
  column_open: "Water-column opens",
  column_depth_set: "Column depth sets",
};

const WINDOWS = [7, 14, 30, 90];

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function htmlResponse(inner, status) {
  return new Response(
    `<!doctype html><html lang="en"><head><meta charset="utf-8">` +
    `<meta name="viewport" content="width=device-width, initial-scale=1">` +
    `<meta name="robots" content="noindex,nofollow">` +
    `<title>ShouldIDive · usage</title><style>${STYLE}</style></head>` +
    `<body>${inner}</body></html>`,
    {
      status: status || 200,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    }
  );
}

const STYLE = `
:root{color-scheme:dark}
body{margin:0;padding:28px 22px;background:#0f1722;color:#e7eef7;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
h1{font-size:20px;margin:0 0 2px}
.sub{color:#8aa0b6;font-size:12.5px;margin:0 0 18px}
.windows{margin:0 0 18px;display:flex;gap:8px;flex-wrap:wrap}
.windows a{padding:4px 11px;border-radius:999px;border:1px solid #2b3a4d;
  color:#cfe0f2;text-decoration:none;font-size:12.5px}
.windows a.on{background:#1d6fe0;border-color:#1d6fe0;color:#fff;font-weight:600}
table{border-collapse:collapse;width:100%;margin:0 0 26px;font-variant-numeric:tabular-nums}
caption{text-align:left;font-weight:700;font-size:13px;color:#9fb4c9;
  text-transform:uppercase;letter-spacing:.04em;padding:0 0 8px}
th,td{padding:7px 10px;border-bottom:1px solid #1c2a3a;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{color:#8aa0b6;font-weight:600;border-bottom:1px solid #2b3a4d}
tbody tr:hover{background:#16212f}
.num{font-weight:600}.zero{color:#46586b}
.foot{color:#6f859b;font-size:11.5px;margin-top:8px}
`;

function totalsTable(totals) {
  const rows = Object.entries(totals).sort((a, b) => b[1].total - a[1].total);
  if (!rows.length) return `<p class="sub">No events recorded in this window yet.</p>`;
  const body = rows.map(([name, c]) => (
    `<tr><td>${esc(LABELS[name] || name)}</td>` +
    `<td class="num">${c.total.toLocaleString()}</td>` +
    `<td>${c.unique.toLocaleString()}</td></tr>`
  )).join("");
  return `<table><caption>Totals (window)</caption>` +
    `<thead><tr><th>Event</th><th>Total</th><th>Unique sessions</th></tr></thead>` +
    `<tbody>${body}</tbody></table>`;
}

function dailyTable(days, totals) {
  // Columns = event names present in the window, ordered by total desc.
  const cols = Object.entries(totals).sort((a, b) => b[1].total - a[1].total).map(([n]) => n);
  if (!cols.length) return "";
  const head = `<tr><th>Date</th>${cols.map((n) => `<th>${esc(LABELS[n] || n)}</th>`).join("")}</tr>`;
  const body = days.map((d) => {
    const cells = cols.map((n) => {
      const v = d.events[n] ? d.events[n].total : 0;
      return `<td class="${v ? "num" : "zero"}">${v ? v.toLocaleString() : "·"}</td>`;
    }).join("");
    return `<tr><td>${esc(d.date)}</td>${cells}</tr>`;
  }).join("");
  return `<table><caption>By day</caption><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

export async function onRequestGet({ request, env }) {
  const denied = gate(request, env);
  if (denied === "unconfigured") {
    return htmlResponse(`<h1>Usage</h1><p class="sub">Stats token isn't configured on the server yet.</p>`, 503);
  }
  if (denied) {
    return htmlResponse(`<h1>Usage</h1><p class="sub">Access denied. Append <code>?key=YOUR_TOKEN</code> to the URL.</p>`, 401);
  }
  if (!env || !env.ANALYTICS_KV) {
    return htmlResponse(`<h1>Usage</h1><p class="sub">Analytics storage isn't bound yet (ANALYTICS_KV). Once the KV namespace is bound to the Pages project, data will appear here.</p>`, 503);
  }

  const url = new URL(request.url);
  const key = url.searchParams.get("key") || "";
  let days = parseInt(url.searchParams.get("days") || "14", 10);
  if (!Number.isFinite(days) || days < 1) days = 14;
  days = Math.min(days, 90);

  const { days: perDay, totals } = await aggregate(env, days);

  const winLinks = WINDOWS.map((w) => {
    const cls = w === days ? "on" : "";
    return `<a class="${cls}" href="/stats?days=${w}&key=${encodeURIComponent(key)}">${w}d</a>`;
  }).join("");

  const inner =
    `<h1>ShouldIDive · usage</h1>` +
    `<p class="sub">Last ${days} days · UTC · generated ${esc(new Date().toISOString().slice(0, 16).replace("T", " "))}</p>` +
    `<div class="windows">${winLinks}</div>` +
    totalsTable(totals) +
    dailyTable(perDay, totals) +
    `<p class="foot">Privacy-preserving: no cookies, no IPs, no PII. "Unique sessions" counts random per-tab ids that expire on tab close. 90-day retention.</p>`;

  return htmlResponse(inner, 200);
}

export async function onRequest({ request, env }) {
  if (request.method === "GET") return onRequestGet({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
