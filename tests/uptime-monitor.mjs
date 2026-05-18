#!/usr/bin/env node
/**
 * Standalone Node uptime probe — invoked by .github/workflows/uptime-monitor.yml
 *
 * Why a Node script (and not curl in YAML):
 *   Cloudflare's bot detection on GHA runner IPs returns HTTP 403 to
 *   plain curl regardless of User-Agent — it fingerprints the TLS
 *   handshake (curl's JA3 differs sharply from real browser stacks).
 *   Node's built-in `fetch` uses undici, whose TLS fingerprint isn't
 *   on CF's blocklist for our zone (verified via live-cp-manifest
 *   which has been hitting the same site reliably for weeks).
 *
 * Behavior:
 *   * Each URL is probed once. On non-OK, sleeps 30 s and probes again.
 *     Only PAIRED failure escalates — defends against transient CF
 *     edge flutters.
 *   * Per-URL outcome printed to stdout with GH log annotation syntax
 *     (::group::, ::warning::, ::error::) so the run page is the
 *     authoritative debug surface.
 *   * Failure count written to $GITHUB_OUTPUT under key `failed`.
 *     A details summary is also written under key `details` for use
 *     in the issue body the downstream job composes.
 *   * Always exits 0 — the workflow uses the `failed` output (not the
 *     exit code) to decide whether to fire the alert.
 */
import { appendFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";


const PROBES = [
  {
    name: "homepage",
    url: "https://shouldidive.com/",
    // No content predicate — anything 200 is fine for the front door.
  },
  {
    name: "manifest",
    url: "https://shouldidive.com/data/manifest.json",
    // Catch the "200 OK but body is an HTML error page" case.
    contentPredicate: (body) => {
      try {
        const j = JSON.parse(body);
        return Array.isArray(j.bbox) && j.bbox.length === 4;
      } catch {
        return false;
      }
    },
  },
];

const FETCH_TIMEOUT_MS = 30_000;
const RETRY_WAIT_MS = 30_000;

/**
 * Single probe attempt. Returns { ok, msg } — msg is a one-line
 * human-readable status.
 */
async function probeOnce({ name, url, contentPredicate }) {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    // Mirror live-cp-manifest's exact fetch pattern. It uses a
    // clearly bot-like UA and still gets 200 reliably, so a Chrome UA
    // here would be cargo-culting. KEEP this in lockstep with
    // tests/live-checkpoints/live-manifest.mjs — if their probe starts
    // 403'ing too, the fix probably needs to land in CF dashboard.
    //
    // Also: the URL includes a per-request cache-buster query string,
    // which avoids CF edge caching of a previous response (some CF
    // configs cache responses based on path and serve them back
    // including their status code — a cached 403 is a real failure
    // mode).
    const probeUrl = `${url}${url.includes("?") ? "&" : "?"}cb=upm-${Date.now()}`;
    const resp = await fetch(probeUrl, {
      headers: { "User-Agent": "ShoudiDive-UptimeMonitor/1.0" },
      signal: controller.signal,
      redirect: "follow",
    });
    if (resp.status !== 200) {
      return { ok: false, msg: `${name} FAIL: HTTP ${resp.status}` };
    }
    if (contentPredicate) {
      const body = await resp.text();
      if (!contentPredicate(body)) {
        return { ok: false, msg: `${name} FAIL: HTTP 200 but content predicate failed` };
      }
    }
    return { ok: true, msg: `${name} OK: HTTP 200` };
  } catch (err) {
    return { ok: false, msg: `${name} FAIL: ${err.name}: ${err.message}` };
  } finally {
    globalThis.clearTimeout(timer);
  }
}

async function probeWithRetry(spec) {
  const first = await probeOnce(spec);
  if (first.ok) return first;
  console.log(`::warning::${first.msg}`);
  console.log(`${spec.name} first attempt failed; sleeping ${RETRY_WAIT_MS / 1000} s before retry...`);
  await sleep(RETRY_WAIT_MS);
  const second = await probeOnce(spec);
  return second;
}

async function main() {
  let failed = 0;
  const lines = [];

  for (const spec of PROBES) {
    console.log(`::group::${spec.name}`);
    const result = await probeWithRetry(spec);
    if (result.ok) {
      console.log(result.msg);
      lines.push(`${spec.name}: OK`);
    } else {
      console.log(`::warning::${result.msg}`);
      lines.push(`${spec.name}: ${result.msg}`);
      failed += 1;
    }
    console.log("::endgroup::");
  }

  const details = lines.join("\n");

  // Emit GITHUB_OUTPUT for the downstream issue-management jobs.
  if (process.env.GITHUB_OUTPUT) {
    appendFileSync(process.env.GITHUB_OUTPUT, `failed=${failed}\n`);
    appendFileSync(
      process.env.GITHUB_OUTPUT,
      `details<<EOF\n${details}\nEOF\n`,
    );
  }

  // Emit a step summary for easy debugging from the UI.
  if (process.env.GITHUB_STEP_SUMMARY) {
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      `## Uptime probe\n\nFailures this tick: ${failed}\n\n\`\`\`\n${details}\n\`\`\`\n`,
    );
  }

  if (failed > 0) {
    console.log(
      `::error::${failed} probe(s) failed. ` +
        `Downstream job will open / update the site-down issue.`,
    );
  } else {
    console.log("All probes OK.");
  }

  // ALWAYS exit 0 — see workflow comment for rationale.
  process.exit(0);
}

await main();
