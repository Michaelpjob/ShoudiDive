#!/usr/bin/env node
// Pull Track-a-paddy run logs out of ANALYTICS_KV as a CSV.
//
//   node scripts/pull-paddy-runs.mjs [--days 30] [--out paddy-runs.csv]
//
// Reads the `ev/<day>/paddy_track_run/<session>/<uuid>` keys written by
// functions/api/analytics/event.js and emits one row per run: the day,
// an anonymous session id, country code, viewport class, the ENTERED
// coordinates, the parse format, and whether the position was typed or
// tapped on the map.
//
// Requires a wrangler login with KV read access (same account that owns
// the shouldidive Pages project). Retention is 90 days — the ingest
// writes every key with that TTL, so this can never see further back.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

const NAMESPACE_ID = "e6eec2aa8e73403bb962a78c4d747e00"; // ANALYTICS_KV
const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : dflt;
};
const days = parseInt(opt("days", "90"), 10);
const out = opt("out", "paddy-runs.csv");

const wrangler = (...a) =>
  execFileSync("npx", ["--yes", "wrangler", ...a], { encoding: "utf8", shell: true, stdio: ["ignore", "pipe", "pipe"] });

function json(raw) {
  const i = raw.indexOf("[") >= 0 && (raw.indexOf("[") < raw.indexOf("{") || raw.indexOf("{") < 0)
    ? raw.indexOf("[") : raw.indexOf("{");
  return JSON.parse(raw.slice(i));
}

const since = new Date(Date.now() - days * 86400e3).toISOString().slice(0, 10);
console.log(`listing paddy_track_run keys since ${since}…`);
const keys = json(wrangler("kv", "key", "list", "--namespace-id", NAMESPACE_ID, "--prefix", "ev/"))
  .map((k) => k.name)
  .filter((n) => {
    const p = n.split("/");
    return p[2] === "paddy_track_run" && p[1] >= since;
  });
console.log(`${keys.length} run(s) recorded`);

const rows = [["day", "session", "country", "viewport", "lat", "lng", "format", "via"]];
for (const name of keys) {
  const [, day, , session] = name.split("/");
  try {
    const v = JSON.parse(wrangler("kv", "key", "get", name, "--namespace-id", NAMESPACE_ID));
    const p = v.props || {};
    rows.push([day, session, v.cc || "", v.vp || "", p.lat ?? "", p.lng ?? "", p.fmt || "", p.via || ""]);
  } catch (e) {
    console.error(`  skip ${name}: ${e.message.split("\n")[0]}`);
  }
}
writeFileSync(out, rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n") + "\n");
console.log(`wrote ${out} (${rows.length - 1} rows)`);
