// Vessel groups behind the Find box (functions/api/fleet/_groups.js). The
// cases are real hulls from the SoCal AIS archive, so a rule change that
// moves one of them shows up here.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { classify, GROUPS, normName } from "../functions/api/fleet/_groups.js";
import { FLEET, LOOKALIKES, FLEET_LIST } from "../functions/api/fleet/_roster_lists.js";

const group = (x) => classify(x).g;

test("the generated roster lists match roster.json", () => {
  const r = JSON.parse(readFileSync(new URL("../fleet-model/data/roster.json", import.meta.url), "utf8"));
  const fleet = r.boats.filter((b) => b.mmsi).map((b) => String(b.mmsi)).sort();
  assert.deepEqual(Object.keys(FLEET).sort(), fleet, "rerun python fleet-model/vessel_lists.py");
  const cands = r.candidates.filter((c) => c.mmsi && !FLEET[String(c.mmsi)]).map((c) => String(c.mmsi)).sort();
  assert.deepEqual(Object.keys(LOOKALIKES).sort(), [...new Set(cands)].sort(), "rerun python fleet-model/vessel_lists.py");
  const names = [...new Set(r.unmatched_fleet_list.map((u) => normName(u.name)).filter(Boolean))].sort();
  assert.deepEqual(Object.keys(FLEET_LIST).sort(), names, "rerun python fleet-model/vessel_lists.py");
});

test("every group key is known and labelled", () => {
  assert.equal(new Set(GROUPS.map((g) => g[0])).size, GROUPS.length);
  for (const [k, label] of GROUPS) assert.ok(k && label);
});

test("lists beat the AIS type", () => {
  const roster = Object.keys(FLEET)[0];
  assert.equal(group({ mmsi: roster, n: "ANY", ty: 37, len: 8 }), "fleet");
  // NOAA's Shearwater broadcasts type 30; the other SHEARWATER (15 m) shares a
  // fleet-list name, so it waits for review as a possible sportfisher
  assert.equal(group({ mmsi: "366875820", n: "SHEARWATER", ty: 30, len: 20 }), "research");
  assert.equal(group({ mmsi: "368169340", n: "SHEARWATER", ty: 30, len: 15 }), "sport");
  assert.equal(group({ mmsi: "368244550", n: "BOB & BETTY BEYSTER" }), "research");
  assert.equal(group({ mmsi: "338010925", n: "OCEAN RANGER", ty: 60, len: 30 }), "gov");
});

test("name, MMSI block and call sign rules find government hulls", () => {
  assert.equal(group({ mmsi: "366745470", n: "RESCUE BOAT 128", ty: 37, len: 8 }), "gov");
  assert.equal(group({ mmsi: "338145697", n: "BAYWATCH 18", ty: 50, len: 10 }), "gov");
  assert.equal(group({ mmsi: "366999662", n: "CG HALIBUT", ty: 55, len: 26 }), "gov");
  assert.equal(group({ mmsi: "338131454", n: "FIREBOAT 110", ty: 91 }), "gov");
  assert.equal(group({ mmsi: "345000000", n: "ARM REVOLUCION", ty: 35 }), "gov");
  assert.equal(group({ mmsi: "369914057", n: "LUCY STONE", ty: 80, len: 228, cs: "NLSE" }), "gov");    // federal block
  assert.equal(group({ mmsi: "338921000", n: "CG GEORGE COBB", ty: 99, len: 53, cs: "NTVY" }), "gov");
  assert.equal(group({ mmsi: "367219000", n: "TANKER 200", ty: 89, len: 206, cs: "NLUP" }), "gov");  // N call sign
  // look-alike words on private boats stay private
  assert.equal(group({ mmsi: "338208542", n: "DAWN PATROL", ty: 36 }), "private");
  assert.equal(group({ mmsi: "368168140", n: "EMOTIONAL RESCUE", ty: 37, len: 15 }), "private");
  assert.equal(group({ mmsi: "338155717", n: "BOHEMIAN NAVY", ty: 37, len: 8 }), "private");
  assert.equal(group({ mmsi: "368102930", n: "NOW", ty: 36, len: 10, cs: "NOW" }), "private");
});

test("a fleet-list name makes a possible sportfisher only when the hull looks the part", () => {
  const lm = Object.keys(LOOKALIKES)[0];
  assert.equal(group({ mmsi: lm, n: "X", ty: 60, len: 24 }), "sport");
  assert.equal(group({ mmsi: lm, n: "X", ty: 89, len: 209 }), "ship");       // a tanker that shares a name
  assert.equal(group({ mmsi: lm, n: "X", ty: 36, len: 14 }), "private");     // a sailboat that shares a name
  const name = Object.keys(FLEET_LIST)[0];
  const c = classify({ mmsi: "338000001", n: name, ty: 37, len: 16 });
  assert.equal(c.g, "sport");
  assert.ok(c.review && c.why.includes("fleet list"));
});

test("AIS type and length decide the rest", () => {
  assert.equal(group({ mmsi: "338362956", n: "MADAM KARMA", ty: 30, len: 5 }), "private");
  assert.equal(group({ mmsi: "367378390", n: "RIVAL", ty: 30, len: 35 }), "fish");
  assert.equal(group({ mmsi: "368024590", n: "BOTTOM SCRATCHER", ty: 34, len: 21 }), "dive");
  assert.equal(group({ mmsi: "367000001", n: "HARBOR CRUISE", ty: 60, len: 24 }), "dive");
  assert.equal(group({ mmsi: "367000002", n: "BIG CRUISE SHIP", ty: 60, len: 289 }), "ship");
  assert.equal(group({ mmsi: "367000003", n: "SPIRITOFSANDIEGO", ty: 6, len: 44 }), "dive");
  assert.equal(group({ mmsi: "367000004", n: "TUG", ty: 52, len: 25 }), "work");
  assert.equal(group({ mmsi: "367000005", n: "BOX SHIP", ty: 71, len: 366 }), "ship");
  assert.equal(group({ mmsi: "367000006", n: "FAST FERRY", ty: 40, len: 45 }), "ship");
  assert.equal(group({ mmsi: "367000007", n: "OFFSHORE", ty: 90, len: 49 }), "work");
  assert.equal(group({ mmsi: "367000008", n: "SAILOR", ty: 36, len: 12 }), "private");
  assert.equal(group({ mmsi: "367000009", n: "MYSTERY" }), "unknown");
  assert.equal(group({ mmsi: "367000010", n: "ODD", ty: 38, len: 23 }), "unknown");
});

test("every result explains itself", () => {
  for (const x of [{ mmsi: "1" }, { mmsi: "367000005", ty: 71, len: 300 }, { mmsi: "366875820" }]) {
    const c = classify(x);
    assert.ok(typeof c.why === "string" && c.why.length > 3);
    assert.equal(typeof c.review, "boolean");
  }
});
