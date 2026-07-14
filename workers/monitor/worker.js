/**
 * shouldidive-monitor — Cloudflare Worker uptime + freshness monitor.
 *
 * Replaces the Actions-based uptime-monitor.yml. Rationale: during the
 * 2026-07-10 Actions budget exhaustion the uptime monitor and alert
 * router were blocked by the SAME budget that froze the data pipeline,
 * so the outage generated zero alerts. Monitoring must not share a
 * failure domain with the thing it monitors. This Worker runs on
 * Cloudflare's free plan, independent of GitHub Actions.
 *
 * What it does, every 5 minutes (cron):
 *   1. Probes https://shouldidive.com/ and /data/manifest.json.
 *      On non-OK: waits 15 s and probes once more (flake protection —
 *      mirrors tests/uptime-monitor.mjs). Paired failure = down.
 *   2. Parses manifest generated_at; older than STALE_HOURS (36, same
 *      criterion as health-check.yml's critical finding) = stale.
 *   3. Syncs a rolling GitHub issue per condition (labels: site-down /
 *      data-stale) — open/comment on failure, auto-close on recovery.
 *      Same alert channel as before: GitHub issue → owner email.
 *      Honors the `monitoring-paused` label escape hatch.
 *
 * GET /status runs the same probes on demand and returns JSON —
 * manual verification without waiting for the cron.
 *
 * Secrets (wrangler secret put): GITHUB_TOKEN — fine-grained PAT,
 * repo Michaelpjob/ShoudiDive, permission Issues: Read & Write.
 * Without it the Worker still probes; it just can't manage issues
 * (probe results remain visible via /status and the CF dashboard logs).
 */

const REPO = "Michaelpjob/ShoudiDive";
const PROBES = [
  { name: "homepage", url: "https://shouldidive.com/" },
  { name: "manifest", url: "https://shouldidive.com/data/manifest.json" },
  { name: "paddies", url: "https://shouldidive.com/paddies/data.json" },
];
const STALE_HOURS = 36; // matches health-check.yml's critical threshold
// Paddies rebuilds once daily (refresh-paddies.yml, 09:30 UTC). 36h =
// one fully missed run + slack. The 2026-07-10→13 budget outage left
// the tool on a 4-day-old snapshot with zero alerts — this probe is
// what would have caught it.
const PADDIES_STALE_HOURS = 36;
const RETRY_DELAY_MS = 15_000;
const UA = "shouldidive-monitor-worker/1.0 (+https://shouldidive.com)";

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runChecks(env, { source: "cron" }));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/status") {
      return new Response("shouldidive-monitor. GET /status for probe results.", { status: 404 });
    }
    // On-demand run never mutates issues — read-only view for humans.
    const report = await runChecks(env, { source: "manual", dryRun: true });
    return new Response(JSON.stringify(report, null, 2), {
      headers: { "content-type": "application/json" },
    });
  },
};

async function runChecks(env, { source, dryRun = false }) {
  const report = { at: new Date().toISOString(), source, probes: [], stale: null, paddies: null };
  const bodies = {};

  for (const probe of PROBES) {
    let result = await probeOnce(probe.url);
    if (!result.ok) {
      await sleep(RETRY_DELAY_MS);
      result = await probeOnce(probe.url);
      result.retried = true;
    }
    if (result.ok) bodies[probe.name] = result.body;
    delete result.body;
    report.probes.push({ name: probe.name, url: probe.url, ...result });
  }

  const down = report.probes.filter((p) => !p.ok);

  // Freshness: timestamp age vs per-surface budget. Only meaningful when
  // the surface is reachable — an unreachable site is already "down".
  report.stale = staleCheck(bodies.manifest, (m) => m.generated_at, STALE_HOURS, "generated_at");
  report.paddies = staleCheck(bodies.paddies, (p) => p.model_meta?.build_utc, PADDIES_STALE_HOURS, "build_utc");

  if (!dryRun) {
    const anyStale = report.stale?.breach === true || report.paddies?.breach === true;
    await syncIssue(env, "site-down", down.length > 0, siteDownBody(report), report);
    await syncIssue(env, "data-stale", anyStale, dataStaleBody(report), report);
  }

  console.log(JSON.stringify({ ...report, down: down.length }));
  return report;
}

// Parse a JSON body, extract a UTC timestamp via `pick`, and grade its
// age against `budgetHours`. Unreachable body → null (the liveness probe
// already covers that); unparseable body or timestamp → breach (a data
// endpoint serving garbage is a data incident, not silence).
function staleCheck(body, pick, budgetHours, fieldName) {
  if (body == null) return null;
  try {
    const parsed = JSON.parse(body);
    const raw = pick(parsed);
    const t = Date.parse(raw);
    const ageHours = Number.isNaN(t) ? null : (Date.now() - t) / 3_600_000;
    return {
      [fieldName]: raw ?? null,
      age_hours: ageHours == null ? null : Math.round(ageHours * 10) / 10,
      budget_hours: budgetHours,
      breach: ageHours == null || ageHours > budgetHours,
    };
  } catch (e) {
    return { error: `unparseable: ${e.message}`, budget_hours: budgetHours, breach: true };
  }
}

async function probeOnce(url) {
  const started = Date.now();
  try {
    // Cache-buster mirrors tests/uptime-monitor.mjs — we want origin
    // health, not a CDN-cached copy of a dead origin.
    const bust = `${url.includes("?") ? "&" : "?"}uptime=${Date.now()}`;
    const res = await fetch(url + bust, {
      headers: { "user-agent": UA },
      signal: AbortSignal.timeout(30_000),
      cf: { cacheTtl: 0 },
    });
    const body = res.ok ? await res.text() : null;
    return { ok: res.ok, status: res.status, ms: Date.now() - started, body };
  } catch (e) {
    return { ok: false, status: 0, ms: Date.now() - started, error: e.message };
  }
}

function siteDownBody(report) {
  const lines = report.probes.map(
    (p) => `- **${p.name}** ${p.ok ? "OK" : "FAILED"} (status ${p.status}${p.error ? `, ${p.error}` : ""}, ${p.ms} ms${p.retried ? ", after retry" : ""}) — ${p.url}`
  );
  return [
    "Paired-failure probe from the Cloudflare monitor Worker (independent of GitHub Actions).",
    "",
    ...lines,
    "",
    `_Probed ${report.at}. Add label \`monitoring-paused\` to silence during planned maintenance._`,
  ].join("\n");
}

function dataStaleBody(report) {
  const s = report.stale || {};
  const p = report.paddies || {};
  const lines = ["The site is up, but a data surface has stopped refreshing:", ""];
  if (s.breach) {
    lines.push(`- **Main manifest** \`generated_at\` is **${s.age_hours ?? "?"} h old** (budget ${STALE_HOURS} h): \`${s.generated_at ?? s.error ?? "unparseable"}\` — check refresh-*-data / refresh-*-wind runs.`);
  }
  if (p.breach) {
    lines.push(`- **Paddies tool** \`model_meta.build_utc\` is **${p.age_hours ?? "?"} h old** (budget ${PADDIES_STALE_HOURS} h): \`${p.build_utc ?? p.error ?? "unparseable"}\` — check refresh-paddies.yml runs. Stale here means the -3d…+2d drift window is anchored days in the past.`);
  }
  lines.push("", `_Probed ${report.at} by the Cloudflare monitor Worker. Add label \`monitoring-paused\` to silence._`);
  return lines.join("\n");
}

/**
 * Rolling-issue sync, mirroring uptime-monitor.yml semantics:
 * failing → open (or comment on) the open issue with `label`;
 * healthy → close it with a recovery comment. `monitoring-paused`
 * on the open issue suppresses comments and reopens.
 */
async function syncIssue(env, label, failing, body, report) {
  if (!env.GITHUB_TOKEN) {
    if (failing) console.log(`ALERT (no GITHUB_TOKEN, cannot file issue): ${label}`);
    return;
  }
  const gh = (path, init = {}) =>
    fetch(`https://api.github.com${path}`, {
      ...init,
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "user-agent": UA,
        ...(init.body ? { "content-type": "application/json" } : {}),
      },
    });

  const listRes = await gh(`/repos/${REPO}/issues?labels=${label}&state=open&per_page=5`);
  if (!listRes.ok) {
    console.log(`issue list failed for ${label}: ${listRes.status}`);
    return;
  }
  const open = await listRes.json();
  const existing = open[0];
  const paused = existing?.labels?.some((l) => (l.name || l) === "monitoring-paused");

  if (failing) {
    if (paused) return console.log(`${label}: failing but monitoring-paused — silent`);
    if (existing) {
      await gh(`/repos/${REPO}/issues/${existing.number}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: `Still failing as of ${report.at}.\n\n${body}` }),
      });
    } else {
      await gh(`/repos/${REPO}/issues`, {
        method: "POST",
        body: JSON.stringify({
          title: label === "site-down" ? "shouldidive.com is DOWN (Worker probe)" : "Live data is STALE (Worker probe)",
          body,
          labels: [label],
        }),
      });
    }
  } else if (existing && !paused) {
    await gh(`/repos/${REPO}/issues/${existing.number}/comments`, {
      method: "POST",
      body: JSON.stringify({ body: `Recovered — probes healthy as of ${report.at}. Auto-closing.` }),
    });
    await gh(`/repos/${REPO}/issues/${existing.number}`, {
      method: "PATCH",
      body: JSON.stringify({ state: "closed" }),
    });
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
