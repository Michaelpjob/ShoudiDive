/**
 * Live health dashboard for shouldidive.com — served at the Worker root.
 *
 * Renders, at request time (no build step, no storage):
 *   1. Site probes — homepage / manifest / paddies reachability + latency.
 *   2. Data freshness — per-layer observation age vs budget for CA + Baja,
 *      plus the paddies drift snapshot. Budgets mirror the pipeline's
 *      check_manifest_freshness.py + src/lib/confidence.js so this page
 *      and CI agree on what "stale" means.
 *   3. Pipeline runs — last GitHub Actions run per key workflow, failure
 *      streaks, and a failure CLASS for the most recent red run of each:
 *        code        — build/lint/test/scan steps failed
 *        connection  — a Fetch/Probe step failed (upstream feed trouble)
 *        stale-data  — a freshness gate reddened (honest-staleness alarm)
 *   4. Open alert issues — site-down / data-stale / live-deploy-broken /
 *      severity:* rolling issues.
 *
 * Everything is fetched live; GH API responses ride Cloudflare's edge
 * cache for 60s so a reload storm can't burn the rate limit.
 */

const REPO = "Michaelpjob/ShoudiDive";
const UA = "shouldidive-monitor-worker/1.0 (+https://shouldidive.com)";

// Observation-age budgets (days) per layer — mirrors LAYER_DATE_MAX_DAYS
// in pipeline/check_manifest_freshness.py. A layer past budget is the
// pipeline failing to land data, not necessarily the source's fault.
const LAYER_BUDGET_DAYS = { sst: 4, chl: 7, kd490: 14, wind: 1, viz: 2, wave: 2, precip: 3 };
// Summary generated_at budgets (hours) — mirrors SUMMARY_MAX_HOURS.
const SUMMARY_BUDGET_HOURS = { wind5d: 8, swell5d: 30, current5d: 30 };
const PADDIES_BUDGET_HOURS = 36;

// Key workflows to surface, in display order. Names must match the
// workflow `name:` fields exactly (they're the API's grouping key).
const WORKFLOWS = [
  "Refresh CA — wind (hourly)",
  "Refresh baja — wind (hourly)",
  "Refresh CA — data (daily)",
  "Refresh baja — data (daily)",
  "Refresh paddies (kelp-drift, daily)",
  "Ingest ground-truth observations",
  "Deploy production (code-only fast path)",
  "Verify live deploy",
  "Alert router",
  "Dev checks",
  "Semgrep",
];

const ALERT_LABELS = ["site-down", "data-stale", "live-deploy-broken"];

export async function renderDashboard(env) {
  const [ca, baja, paddies, home, runs, issues] = await Promise.all([
    getJson("https://shouldidive.com/data/manifest.json"),
    getJson("https://shouldidive.com/data/baja/manifest.json"),
    getJson("https://shouldidive.com/paddies/data.json"),
    probe("https://shouldidive.com/"),
    ghJson(env, `/repos/${REPO}/actions/runs?per_page=100`),
    ghJson(env, `/repos/${REPO}/issues?state=open&per_page=50`),
  ]);

  const probes = [
    { name: "homepage", ...home },
    { name: "CA manifest", ok: ca.ok, ms: ca.ms, status: ca.status },
    { name: "Baja manifest", ok: baja.ok, ms: baja.ms, status: baja.status },
    { name: "paddies data", ok: paddies.ok, ms: paddies.ms, status: paddies.status },
  ];

  const freshness = [
    ...layerRows("CA", ca.body),
    ...layerRows("Baja", baja.body),
    paddiesRow(paddies.body),
  ].filter(Boolean);

  const pipelines = await pipelineRows(env, runs.body);
  const alerts = alertRows(issues.body);

  const problems = [
    ...probes.filter((p) => !p.ok).map((p) => `${p.name} unreachable`),
    ...freshness.filter((f) => f.state === "stale").map((f) => `${f.region} ${f.layer} stale`),
    ...pipelines.filter((w) => w.streak >= 2).map((w) => `${w.name} failing x${w.streak}`),
    ...alerts.map((a) => `open alert: ${a.title}`),
  ];

  return html(probes, freshness, pipelines, alerts, problems, runs.ok);
}

// ---- data gathering ---------------------------------------------------

async function probe(url) {
  const t = Date.now();
  try {
    const res = await fetch(`${url}${url.includes("?") ? "&" : "?"}dash=${Date.now()}`, {
      headers: { "user-agent": UA },
      signal: AbortSignal.timeout(15_000),
      cf: { cacheTtl: 0 },
    });
    return { ok: res.ok, status: res.status, ms: Date.now() - t };
  } catch (e) {
    return { ok: false, status: 0, ms: Date.now() - t, error: e.message };
  }
}

async function getJson(url) {
  const t = Date.now();
  try {
    const res = await fetch(`${url}?dash=${Date.now()}`, {
      headers: { "user-agent": UA },
      signal: AbortSignal.timeout(15_000),
      cf: { cacheTtl: 0 },
    });
    const body = res.ok ? await res.json() : null;
    return { ok: res.ok, status: res.status, ms: Date.now() - t, body };
  } catch (e) {
    return { ok: false, status: 0, ms: Date.now() - t, body: null, error: e.message };
  }
}

// GitHub API GET with 60s edge cache. Uses the issues-scoped token when it
// works, silently falls back to unauthenticated (the repo is public).
async function ghJson(env, path, ttl = 60) {
  const attempt = async (withToken) => {
    const headers = { accept: "application/vnd.github+json", "user-agent": UA };
    if (withToken && env.GITHUB_TOKEN) headers.authorization = `Bearer ${env.GITHUB_TOKEN}`;
    return fetch(`https://api.github.com${path}`, { headers, cf: { cacheTtl: ttl, cacheEverything: true } });
  };
  try {
    let res = await attempt(true);
    if (res.status === 401 || res.status === 403) res = await attempt(false);
    return { ok: res.ok, body: res.ok ? await res.json() : null };
  } catch (e) {
    return { ok: false, body: null, error: e.message };
  }
}

function ageDays(dateStr) {
  const t = Date.parse(dateStr?.length === 10 ? `${dateStr}T00:00:00Z` : dateStr);
  return Number.isNaN(t) ? null : (Date.now() - t) / 86_400_000;
}

function grade(age, budget) {
  if (age == null) return "unknown";
  if (age > budget) return "stale";
  if (age > budget * 0.6) return "aging";
  return "ok";
}

function layerRows(region, manifest) {
  if (!manifest?.layers) return [{ region, layer: "manifest", detail: "unreachable", state: "stale" }];
  const rows = [];
  for (const [layer, budget] of Object.entries(LAYER_BUDGET_DAYS)) {
    const info = manifest.layers[layer];
    if (!info) continue;
    const dates = info.windows?.["1d"]?.dates || info.windows?.["2d"]?.dates || [];
    const latest = dates[dates.length - 1] || info.generated_at;
    const age = ageDays(latest);
    rows.push({
      region, layer,
      detail: `obs ${latest ? String(latest).slice(0, 10) : "?"} (${age == null ? "?" : age.toFixed(1)}d, budget ${budget}d)${info.source ? ` · ${info.source}` : ""}`,
      state: grade(age, budget),
    });
  }
  for (const [layer, budgetH] of Object.entries(SUMMARY_BUDGET_HOURS)) {
    const info = manifest.layers[layer];
    if (!info) continue;
    const gen = info.generated_at || info.windows?.["1d"]?.generated_at;
    const ageH = gen ? ageDays(gen) * 24 : null;
    rows.push({
      region, layer,
      detail: `built ${ageH == null ? "?" : ageH.toFixed(1)}h ago (budget ${budgetH}h)`,
      state: grade(ageH, budgetH),
    });
  }
  return rows;
}

function paddiesRow(data) {
  const built = data?.model_meta?.build_utc;
  if (!built) return { region: "Paddies", layer: "drift model", detail: "data.json unreachable", state: "stale" };
  const ageH = ageDays(built) * 24;
  return {
    region: "Paddies", layer: "drift model",
    detail: `built ${ageH.toFixed(1)}h ago (budget ${PADDIES_BUDGET_HOURS}h) · ${data.frames?.length ?? "?"} frames`,
    state: grade(ageH, PADDIES_BUDGET_HOURS),
  };
}

async function pipelineRows(env, runsBody) {
  const runs = runsBody?.workflow_runs || [];
  const byName = new Map();
  for (const r of runs) {
    if (!byName.has(r.name)) byName.set(r.name, []);
    byName.get(r.name).push(r);
  }
  const rows = [];
  let jobLookups = 0;
  for (const name of WORKFLOWS) {
    const list = (byName.get(name) || []).filter((r) => r.status === "completed");
    if (!list.length) {
      rows.push({ name, state: "unknown", detail: "no recent runs", streak: 0 });
      continue;
    }
    const latest = list[0];
    let streak = 0;
    for (const r of list) {
      if (r.conclusion === "failure") streak++;
      else break;
    }
    let klass = "";
    if (latest.conclusion === "failure" && jobLookups < 5) {
      jobLookups++;
      klass = await classifyFailure(env, latest.id);
    }
    rows.push({
      name,
      state: latest.conclusion === "success" ? "ok" : latest.conclusion === "failure" ? "stale" : "aging",
      detail: `${latest.conclusion} · ${new Date(latest.run_started_at).toISOString().slice(5, 16).replace("T", " ")}Z${klass ? ` · ${klass}` : ""}`,
      streak,
      url: latest.html_url,
    });
  }
  return rows;
}

// Classify a failed run by its first failed step name.
async function classifyFailure(env, runId) {
  const jobs = await ghJson(env, `/repos/${REPO}/actions/runs/${runId}/jobs`, 300);
  const steps = (jobs.body?.jobs || []).flatMap((j) => j.steps || []);
  const failed = steps.find((s) => s.conclusion === "failure");
  if (!failed) return "class: blocked/none";
  const n = failed.name.toLowerCase();
  if (n.includes("freshness") || n.includes("gate")) return "class: STALE-DATA (gate)";
  if (n.startsWith("fetch") || n.includes("probe") || n.includes("ingest")) return "class: CONNECTION (upstream)";
  if (n.includes("test") || n.includes("lint") || n.includes("build") || n.includes("semgrep") || n.includes("scan")) return "class: CODE";
  return `class: other (${failed.name.slice(0, 30)})`;
}

function alertRows(issuesBody) {
  return (issuesBody || [])
    .filter((i) => !i.pull_request)
    .filter((i) => (i.labels || []).some((l) => ALERT_LABELS.includes(l.name) || l.name.startsWith("severity:p0") || l.name.startsWith("severity:p1") || l.name.startsWith("severity:p2")))
    .map((i) => ({ title: i.title, url: i.html_url, labels: (i.labels || []).map((l) => l.name).join(", "), updated: i.updated_at }));
}

// ---- rendering ---------------------------------------------------------

const COLORS = { ok: "#22c55e", aging: "#eab308", stale: "#ef4444", unknown: "#64748b" };

function chip(state) {
  return `<span class="chip" style="background:${COLORS[state] || COLORS.unknown}"></span>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function html(probes, freshness, pipelines, alerts, problems, ghOk) {
  const healthy = problems.length === 0;
  const now = new Date().toISOString().slice(0, 16).replace("T", " ") + "Z";
  return `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>ShouldIDive health</title>
<style>
  body{margin:0;padding:24px;background:#0b1220;color:#dbe4f0;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  h1{font-size:18px;margin:0 0 4px} h2{font-size:13px;margin:22px 0 8px;color:#8aa0bd;text-transform:uppercase;letter-spacing:.08em}
  .banner{padding:10px 14px;border-radius:8px;margin:14px 0;font-weight:600;
    background:${healthy ? "#052e1a" : "#3a0a0a"};border:1px solid ${healthy ? "#14532d" : "#7f1d1d"}}
  table{border-collapse:collapse;width:100%;max-width:980px} td,th{padding:5px 10px;text-align:left;border-bottom:1px solid #1e293b;vertical-align:top}
  th{color:#8aa0bd;font-weight:500} .chip{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}
  a{color:#7dd3fc;text-decoration:none} a:hover{text-decoration:underline}
  .muted{color:#64748b} .wrap{overflow-x:auto}
</style></head><body>
<h1>ShouldIDive · live health</h1>
<div class="muted">generated ${now} · auto-refreshes every 5 min · <a href="/status">raw JSON</a> · <a href="https://github.com/${REPO}/actions">actions</a></div>
<div class="banner">${healthy ? "✓ All systems normal" : `⚠ ${problems.length} issue${problems.length > 1 ? "s" : ""}: ${esc(problems.slice(0, 4).join(" · "))}${problems.length > 4 ? " · …" : ""}`}</div>

<h2>Site</h2><div class="wrap"><table>
${probes.map((p) => `<tr><td>${chip(p.ok ? "ok" : "stale")}${esc(p.name)}</td><td>${p.ok ? `${p.status} · ${p.ms} ms` : `<b>DOWN</b> (${p.status || esc(p.error || "error")})`}</td></tr>`).join("")}
</table></div>

<h2>Data freshness</h2><div class="wrap"><table>
<tr><th></th><th>region</th><th>layer</th><th>detail</th></tr>
${freshness.map((f) => `<tr><td>${chip(f.state)}</td><td>${esc(f.region)}</td><td>${esc(f.layer)}</td><td>${esc(f.detail)}</td></tr>`).join("")}
</table></div>

<h2>Pipelines ${ghOk ? "" : '<span class="muted">(GitHub API unavailable)</span>'}</h2><div class="wrap"><table>
<tr><th></th><th>workflow</th><th>last completed run</th><th>fail streak</th></tr>
${pipelines.map((w) => `<tr><td>${chip(w.state)}</td><td>${w.url ? `<a href="${esc(w.url)}">${esc(w.name)}</a>` : esc(w.name)}</td><td>${esc(w.detail)}</td><td>${w.streak >= 2 ? `<b>${w.streak}</b>` : w.streak || ""}</td></tr>`).join("")}
</table></div>

<h2>Open alerts</h2><div class="wrap"><table>
${alerts.length ? alerts.map((a) => `<tr><td>${chip("stale")}<a href="${esc(a.url)}">${esc(a.title)}</a></td><td class="muted">${esc(a.labels)}</td></tr>`).join("") : `<tr><td>${chip("ok")}none open</td></tr>`}
</table></div>

<div class="muted" style="margin-top:26px">Failure classes: CODE = build/lint/test step · CONNECTION = upstream fetch/probe step · STALE-DATA = freshness gate (honest-staleness alarm, often self-heals). Budgets mirror pipeline/check_manifest_freshness.py.</div>
</body></html>`;
}
