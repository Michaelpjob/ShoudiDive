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
