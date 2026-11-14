// Field reports API (functions/api/reports/*): validation, the moderation
// round trip, and the privacy contract that the public feed never carries a
// reporter's email, name, device or IP hash.
import test from "node:test";
import assert from "node:assert/strict";
import { validate, publicView } from "../functions/api/reports/_lib.js";
import { onRequestPost as submit } from "../functions/api/reports/submit.js";
import { onRequestGet as feed } from "../functions/api/reports/feed.js";
import { onRequest as moderate } from "../functions/api/reports/moderate.js";
import { onRequest as review } from "../functions/reports/review.js";

function kv() {
  const m = new Map();
  return {
    m,
    async get(k) { return m.has(k) ? m.get(k) : null; },
    async put(k, v) { m.set(k, v); },
    async delete(k) { m.delete(k); },
    async list({ prefix }) { return { keys: [...m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true }; },
  };
}
const TOKEN = "t0ken-for-tests";
const today = new Date().toISOString().slice(0, 10);
function post(url, body, ip = "1.2.3.4") {
  return new Request(url, { method: "POST", headers: { "Content-Type": "application/json", "CF-Connecting-IP": ip }, body: JSON.stringify(body) });
}

test("validate accepts each report type and rejects junk", () => {
  const now = Date.now();
  assert.ok(validate({ type: "catch", date: today, lat: 32.85, lng: -117.3, species: ["Yellowtail"] }, now).rec);
  assert.ok(validate({ type: "sighting", date: today, lat: 32.6, lng: -117.4, sighting: "kelp paddy" }, now).rec);
  assert.ok(validate({ type: "bait", date: today, barge: "Mission Bay", bait: "sardine" }, now).rec);
  const d = validate({ type: "dive", date: today, lat: 32.85, lng: -117.27, vis_lo_ft: 20, vis_hi_ft: 12 }, now).rec;
  assert.equal(d.vis_lo_ft, 12);
  assert.equal(d.vis_hi_ft, 20);
  assert.match(validate({ type: "dive", date: today, lat: 32.85, lng: -117.27 }, now).error, /visibility/);
  assert.match(validate({ type: "catch", date: today, lat: 40, lng: -117.3, species: ["yellowtail"] }, now).error, /outside/);
  assert.match(validate({ type: "catch", date: "2020-01-01", lat: 32.8, lng: -117.3, species: ["yellowtail"] }, now).error, /date/);
  assert.match(validate({ type: "lies", date: today }, now).error, /type/);
  assert.match(validate({ type: "catch", date: today, lat: 32.8, lng: -117.3, species: ["kraken"] }, now).error, /species/);
});

test("locations snap to the 0.02 degree grid", () => {
  const r = validate({ type: "sighting", date: today, lat: 32.8571, lng: -117.2891, sighting: "birds working" }, Date.now()).rec;
  assert.equal(r.lat, 32.86);
  assert.equal(r.lng, -117.28);
});

test("publicView never carries reporter details", () => {
  const v = publicView({ id: "x", type: "dive", date: today, lat: 1, lng: 2, vis_lo_ft: 10, vis_hi_ft: 15,
    email: "a@b.co", name: "Pat", deviceId: "dev", ipHash: "abc", submittedAt: 1 });
  for (const k of ["email", "name", "deviceId", "ipHash", "submittedAt"]) assert.ok(!(k in v), k);
  assert.equal(v.vis_hi_ft, 15);
});

test("submit -> pending -> approve -> public feed, without PII", async () => {
  const env = { REPORTS_KV: kv(), MODERATION_TOKEN: TOKEN };
  const body = { type: "dive", date: today, lat: 32.851, lng: -117.271, vis_lo_ft: 15, vis_hi_ft: 20, water_temp_f: 66,
    color: "green", email: "diver@example.com", name: "Samwise Quill", deviceId: "device-xyz-987", notes: "Cove, surge on the outside" };
  const r = await submit({ request: post("https://x/api/reports/submit", body), env });
  const j = await r.json();
  assert.equal(r.status, 200, JSON.stringify(j));
  assert.equal(j.status, "pending");

  let f = await (await feed({ env })).json();
  assert.equal(f.reports.length, 0, "pending reports are not public");

  assert.equal((await moderate({ request: new Request("https://x/api/reports/moderate"), env })).status, 401);
  const pend = await (await moderate({ request: new Request(`https://x/api/reports/moderate?key=${TOKEN}`), env })).json();
  assert.equal(pend.pending.length, 1);
  assert.equal(pend.pending[0].email, "diver@example.com", "moderator sees the email");

  const page = await review({ request: new Request(`https://x/reports/review?key=${TOKEN}`), env });
  assert.equal(page.headers.get("Referrer-Policy"), "no-referrer");
  assert.match(await page.text(), /diver@example\.com/);

  const fd = new FormData();
  fd.set("key", TOKEN); fd.set("id", j.id); fd.set("action", "approve");
  const after = await review({ request: new Request("https://x/reports/review", { method: "POST", body: fd }), env });
  assert.match(await after.text(), /approved/);

  f = await (await feed({ env })).json();
  assert.equal(f.reports.length, 1);
  const pub = JSON.stringify(f);
  assert.ok(!pub.includes("diver@example.com") && !pub.includes("Samwise") && !pub.includes("device-xyz"), "feed leaks PII");
  assert.equal(f.reports[0].vis_lo_ft, 15);
  assert.equal(f.reports[0].confidence, "single report");
});

test("two distinct reporters on the same thing confirm each other", async () => {
  const env = { REPORTS_KV: kv(), MODERATION_TOKEN: TOKEN };
  const base = { type: "bait", date: today, barge: "San Diego Bay", bait: "sardine" };
  const a = await (await submit({ request: post("https://x/api/reports/submit", { ...base, email: "a@x.co", deviceId: "a" }, "9.9.9.1"), env })).json();
  const b = await (await submit({ request: post("https://x/api/reports/submit", { ...base, email: "b@x.co", deviceId: "b" }, "9.9.9.2"), env })).json();
  for (const id of [a.id, b.id]) {
    await moderate({ request: new Request(`https://x/api/reports/moderate?key=${TOKEN}`, { method: "POST", body: JSON.stringify({ id, action: "approve" }) }), env });
  }
  const f = await (await feed({ env })).json();
  assert.equal(f.reports.length, 2);
  assert.ok(f.reports.every((r) => r.confidence === "confirmed"));
});

test("submit rate limits per IP", async () => {
  const env = { REPORTS_KV: kv(), MODERATION_TOKEN: TOKEN };
  const body = { type: "sighting", date: today, lat: 32.6, lng: -117.4, sighting: "porpoise", email: "s@x.co", deviceId: "z" };
  let last;
  for (let i = 0; i < 9; i++) last = await submit({ request: post("https://x/api/reports/submit", body), env });
  assert.equal(last.status, 429);
});
