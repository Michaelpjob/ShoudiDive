// Find-a-boat endpoints: shard parsing for /api/fleet/vessel and the search
// list for /api/fleet/vessels. The shard format is written by
// fleet-model/any_log.py (mmsi,epoch,lat,lon,sog,cog,heading).
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseShard, shardOf } from "../functions/api/fleet/vessel.js";
import { listVessels } from "../functions/api/fleet/vessels.js";

test("shardOf matches the logger's last-two-digits rule", () => {
  assert.equal(shardOf("366123401"), "01");
  assert.equal(shardOf("338000099"), "99");
  assert.equal(shardOf(366123400), "00");
});

test("parseShard keeps one MMSI, inside the window, with nulls for blanks", () => {
  const text = [
    "366123456,1000,32.70000,-117.20000,8.2,270,268",
    "366123456,2000,32.71000,-117.21000,,,",
    "3661234560,2000,1,1,1,1,1",            // a longer MMSI sharing the prefix
    "366123457,2000,32.9,-117.9,1,1,1",
    "366123456,500,32.6,-117.1,0,0,0",      // before the window
    "366123456,2500,bad,-117.2,0,0,0",
    "",
  ].join("\n");
  assert.deepEqual(parseShard(text, "366123456", 900), [
    [1000, 32.7, -117.2, 8.2, 270],
    [2000, 32.71, -117.21, null, null],
  ]);
  assert.deepEqual(parseShard(null, "366123456", 0), []);
});

test("listVessels drops identity-only hulls and sorts newest fix first", () => {
  const idx = { v: {
    "366000001": { n: "OLD", t: 100, ty: 37, len: 12 },
    "366000002": { n: "NEW", t: 300 },
    "366000003": { ty: 30, u: 400 },        // static heard, no position yet
  } };
  assert.deepEqual(listVessels(idx), [[366000002, "NEW", 300, null, null], [366000001, "OLD", 100, 37, 12]]);
  assert.deepEqual(listVessels(null), []);
});
