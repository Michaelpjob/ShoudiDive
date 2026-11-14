// Reports that arrive as words: the shared reader (functions/api/reports/_parse.js),
// the sentence box's parse endpoint, the email Worker and the text-message webhook.
import test from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { extract, parseGps, reportDate, toSubmission, ZONES } from "../functions/api/reports/_parse.js";
import { validate } from "../functions/api/reports/_lib.js";
import { mimeText, fromHeader, twilioSignatureOk } from "../functions/api/reports/_inbound.js";
import { onRequestPost as parse } from "../functions/api/reports/parse.js";
import { onRequestPost as sms } from "../functions/api/reports/sms.js";
import { handleEmail } from "../workers/report-email/worker.js";

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
const pending = (env) => [...env.REPORTS_KV.m.entries()].filter(([k]) => k.startsWith("obs:pending:")).map(([, v]) => JSON.parse(v));
const NOW = Date.parse("2026-10-15T20:00:00Z");

test("the reader finds fish, counts, place, bait and method", () => {
  const x = extract("Overnight trip to the 9 Mile Bank: 45 yellowtail and 3 bluefin tuna on fly-lined sardines and surface iron, kelp paddies in 68 degree water.", NOW);
  assert.deepEqual(x.species, [{ name: "bluefin", count: 3 }, { name: "yellowtail", count: 45 }]);
  assert.deepEqual(x.zones, [6]);
  assert.deepEqual(x.bait, ["sardine"]);
  assert.ok(x.methods.includes("fly-line") && x.methods.includes("surface iron"));
  assert.equal(x.water_temp_f, 68);
  const y = extract("South 9 and the 302 gave up 12 yellowfin in 2026. Left out of Oceanside.", NOW);
  assert.deepEqual(y.zones, [10]);
  assert.deepEqual(y.species, [{ name: "yellowfin", count: 12 }]);
});

test("GPS the way anglers write it", () => {
  const near = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
  assert.ok(near(parseGps("hit at 32 52 x 117 17 this am"), [32 + 52 / 60, -(117 + 17 / 60)]));
  assert.ok(near(parseGps("33 29.271 x 117 48.153 ; marlin"), [33 + 29.271 / 60, -(117 + 48.153 / 60)]));
  assert.ok(near(parseGps("32°52.1'N 117°16.8'W"), [32 + 52.1 / 60, -(117 + 16.8 / 60)]));
  assert.ok(near(parseGps("pin 32.871, -117.281"), [32.871, -117.281]));
  assert.equal(parseGps("45 yellowtail on 30 lb line"), null);
});

test("the day comes from the words, else the day it was sent", () => {
  assert.equal(reportDate("caught yesterday at the islands", NOW), "2026-10-14");
  assert.equal(reportDate("10/12 trip to the 302", NOW), "2026-10-12");
  assert.equal(reportDate("12 yellowtail", NOW), "2026-10-15");
});

test("a sentence becomes a valid submission", () => {
  const { body } = toSubmission("Got 6 dorado and a wahoo on the troll at 32 33 x 117 24, 72 degrees", NOW);
  assert.equal(body.type, "catch");
  const v = validate({ ...body, via: "text" }, NOW);
  assert.ok(v.rec, v.error);
  assert.deepEqual(v.rec.counts, { dorado: 6 });
  assert.equal(v.rec.method, "trolling");
  assert.ok(Math.abs(v.rec.lat - 32.55) <= 0.01);
  const z = toSubmission("Calico bass biting at the Coronados on plastics", NOW).body;
  assert.equal(z.loc, "zone");
  assert.ok(validate({ ...z, via: "web" }, NOW).rec.loc === "zone");
});

test("the reader's zones match the report's zones", () => {
  const py = readFileSync(new URL("../pipeline/reports/zones.py", import.meta.url), "utf8");
  const centers = [...py.matchAll(/\("[^"]+", "(?:inshore|offshore|mexico)", \(([-\d.]+), ([-\d.]+)\)/g)].map((m) => [+m[1], +m[2]]);
  assert.equal(centers.length, ZONES.length);
  ZONES.forEach((z, i) => assert.deepEqual(z[1], centers[i], z[0]));
});

test("the parse endpoint returns a draft and what is missing", async () => {
  const r = await parse({ request: new Request("https://x/api/reports/parse", { method: "POST", body: JSON.stringify({ text: "8 yellowtail on yo-yo iron" }) }) });
  const j = await r.json();
  assert.equal(j.draft.type, "catch");
  assert.deepEqual(j.missing, ["where (a GPS number or a spot name)"]);
});

test("email: the plain part, decoded, without the quoted reply or signature", () => {
  const raw = [
    'From: "Pat Angler" <pat@example.com>', "Subject: 9 mile", "MIME-Version: 1.0",
    'Content-Type: multipart/alternative; boundary="b1"', "", "--b1", "Content-Type: text/plain; charset=utf-8",
    "Content-Transfer-Encoding: quoted-printable", "", "12 yellowtail at the 9 Mile on sardines, 70 degr=", "ees", "", "-- ", "Pat",
    "> old quoted text", "--b1", "Content-Type: text/html", "", "<p>12 yellowtail</p>", "--b1--", "",
  ].join("\r\n");
  assert.equal(mimeText(raw), "12 yellowtail at the 9 Mile on sardines, 70 degrees");
  assert.deepEqual(fromHeader(raw), { name: "Pat Angler", email: "pat@example.com" });
  const b64 = ["From: a@b.co", "Content-Type: text/plain", "Content-Transfer-Encoding: base64", "",
    Buffer.from("3 bluefin at the Tanner on the kite").toString("base64"), ""].join("\r\n");
  assert.equal(mimeText(b64), "3 bluefin at the Tanner on the kite");
});

test("email Worker: a placeable report is queued, anything else bounces with what to add", async () => {
  const env = { REPORTS_KV: kv() };
  const mk = (body) => {
    const raw = ["From: Pat <pat@example.com>", "Subject: report", "Content-Type: text/plain", "", body, ""].join("\r\n");
    return { raw: new Blob([raw]).stream(), from: "pat@example.com", rejected: null, setReject(r) { this.rejected = r; } };
  };
  const ok = mk("Limits of rockfish at San Clemente Island on the dropper loop");
  const r = await handleEmail(ok, env, NOW);
  assert.ok(r.ok && !ok.rejected);
  const p = pending(env);
  assert.equal(p.length, 1);
  assert.equal(p[0].via, "email");
  assert.equal(p[0].loc, "zone");
  assert.equal(p[0].email, "pat@example.com");
  assert.match(p[0].raw_text, /rockfish/);
  const bad = mk("Great day on the water, thanks to the crew");
  await handleEmail(bad, env, NOW);
  assert.match(bad.rejected, /couldn't read/);
});

test("text messages: signature checked, report queued, reply in TwiML", async () => {
  const token = "test-token", url = "https://dev.shouldidive.pages.dev/api/reports/sms";
  const params = { From: "+16195550100", Body: "10 dorado on a paddy at 33 22 x 117 58", MessageSid: "SM1" };
  const sig = createHmac("sha1", token).update(url + Object.keys(params).sort().map((k) => k + params[k]).join("")).digest("base64");
  assert.ok(await twilioSignatureOk(token, url, params, sig));
  assert.ok(!(await twilioSignatureOk(token, url, params, sig.replace(/.$/, "A"))));
  const env = { REPORTS_KV: kv(), TWILIO_AUTH_TOKEN: token };
  const req = (s) => new Request(url, { method: "POST", headers: { "X-Twilio-Signature": s, "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(params) });
  assert.equal((await sms({ request: req("nope"), env })).status, 403);
  const res = await sms({ request: req(sig), env });
  assert.match(await res.text(), /<Message>Thanks/);
  const p = pending(env);
  assert.equal(p.length, 1);
  assert.equal(p[0].email, "text:+16195550100");
  assert.deepEqual(p[0].counts, { dorado: 10 });
  assert.equal((await sms({ request: req(sig), env: { REPORTS_KV: kv() } })).status, 503);
});
