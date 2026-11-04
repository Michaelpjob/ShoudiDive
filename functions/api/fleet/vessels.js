// GET /api/fleet/vessels — every hull the SoCal receivers heard in the last
// week, fleet or not, for the tool's find-a-boat search: {since, updated,
// v:[[mmsi, name, lastFixEpoch, aisType, lengthM], ...]} newest fix first.
// Read from the fleet-any branch's vessels.json; edge-cached two minutes.
import { readAnyIndex, json } from "./_lib.js";

export function listVessels(idx) {
  const v = [];
  for (const [mmsi, x] of Object.entries((idx && idx.v) || {})) {
    if (x.t == null) continue;   // identity heard, no position yet
    v.push([+mmsi, x.n || null, x.t, x.ty || null, x.len || null]);
  }
  v.sort((a, b) => b[2] - a[2]);
  return v;
}

export async function onRequestGet() {
  const idx = await readAnyIndex();
  return json({ since: idx ? idx.since : null, updated: idx ? idx.updated : null, v: listVessels(idx),
                served: new Date().toISOString().slice(0, 19) + "Z" }, 120);
}

export async function onRequest({ request }) {
  if (request.method === "GET") return onRequestGet();
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
