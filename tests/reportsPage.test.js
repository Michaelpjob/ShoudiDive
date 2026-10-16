// Morning Report page contract (public/reports/). The page renders facts built
// by pipeline/reports/build_report.py; these checks pin the honesty rules that
// live on the page side: it never calls conditions "safe", every data string is
// escaped before it reaches innerHTML, and it reads the published data path.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const app = readFileSync(join(root, "public/reports/app.js"), "utf8");
const html = readFileSync(join(root, "public/reports/index.html"), "utf8");

test("the page never uses the word safe or unsafe", () => {
  for (const [name, src] of [["app.js", app], ["index.html", html]]) {
    assert.doesNotMatch(src, /\b(un)?safe\b/i, `${name} says "safe"`);
    assert.doesNotMatch(src, /all clear/i, `${name} says "all clear"`);
  }
});

test("it reads the published report data", () => {
  assert.match(app, /"\/data\/reports\/"/);
  assert.match(app, /index\.json/);
});

test("data strings are escaped before they reach innerHTML", () => {
  assert.match(app, /function esc\(/);
  // Every interpolation of a facts field into markup goes through esc(),
  // chip(), link() or a number formatter; a bare `+ d.why +` would be a hole.
  const bare = app.match(/\+\s*(?:d|f|it|b|p|s|z|h|t|e|sd|w)\.[a-z_]+(?:\.[a-z_]+)*\s*\+/g) || [];
  assert.deepEqual(bare, [], `unescaped facts fields: ${bare.join(" ")}`);
});

test("the page is not indexed while it is a preview", () => {
  assert.match(html, /<meta name="robots" content="noindex"\/>/);
});

test("the report form offers exactly what the field-report API accepts", async () => {
  const lib = await import("../functions/api/reports/_lib.js");
  const grab = (name) => {
    const start = app.indexOf("var " + name + " = [");
    assert.ok(start >= 0, name);
    const open = app.indexOf("[", start);
    return JSON.parse(app.slice(open, app.indexOf("];", open) + 1));
  };
  assert.deepEqual(grab("OBS_SPECIES"), lib.SPECIES);
  assert.deepEqual(grab("OBS_SIGHTINGS"), lib.SIGHTINGS);
  assert.deepEqual(grab("OBS_BAITS"), lib.BAITS);
  assert.deepEqual(grab("OBS_BARGES"), Object.keys(lib.BARGES));
  assert.deepEqual(grab("OBS_COLORS"), lib.COLORS);
  assert.match(app, /"\/api\/reports\/submit"/);
  assert.match(app, /"\/api\/reports\/feed"/);
});

test("visibility uses the main map's colors", () => {
  const overlay = readFileSync(join(root, "src/components/DataOverlay.jsx"), "utf8");
  const block = overlay.slice(overlay.indexOf("const VIZ_RAMP = ["), overlay.indexOf("];", overlay.indexOf("const VIZ_RAMP = [")));
  const main = [...block.matchAll(/ft:\s*(\d+),\s*c:\s*\[\s*(\d+),\s*(\d+),\s*(\d+)\s*\]/g)].map((m) => [+m[1], [+m[2], +m[3], +m[4]]]);
  const start = app.indexOf("var VIS_RAMP = [");
  const ours = JSON.parse(app.slice(app.indexOf("[", start), app.indexOf("];", start) + 1));
  assert.ok(main.length >= 5);
  assert.deepEqual(ours, main);
});

test("diving and spearfishing share one view, and old spearfishing links still land", () => {
  const start = app.indexOf("var VIEWS = [");
  const views = JSON.parse(app.slice(app.indexOf("[", start), app.indexOf("];", start) + 1));
  assert.deepEqual(views.map((v) => v[0]), ["fishing", "diving"]);
  assert.match(app, /view=\(fishing\|diving\|spearfishing\)/);
});
