// GET /api/fleet/tracks — every roster boat's path over the last 24 hours,
// one point per two minutes, oldest first: {boats:[{mmsi,name,pts:[[epochSec,lat,lon,sog],...]}]}.
// The tool draws the part since the boat last left the dock as its current
// trip and the rest as a faint trail. Edge-cached for two minutes.
import { readWindow, epoch, json } from "./_lib.js";

const HOURS = 24;
const STEP_MS = 120e3;

export async function onRequestGet() {
  const now = new Date();
  const { rows, files } = await readWindow(now, HOURS);
  const since = now.getTime() - HOURS * 3600e3;
  const by = new Map();
  for (const p of rows) {
    if (!p.mmsi || p.latitude == null || p.longitude == null) continue;
    const t = epoch(p);
    if (t == null || t < since) continue;
    let b = by.get(p.mmsi);
    if (!b) { b = { mmsi: p.mmsi, name: p.vessel_name || null, raw: [] }; by.set(p.mmsi, b); }
    b.raw.push([t, p.latitude, p.longitude, p.sog == null ? null : Number(p.sog)]);
  }
  const boats = [];
  for (const b of by.values()) {
    b.raw.sort((x, y) => x[0] - y[0]);
    const pts = [];
    let last = -Infinity;
    for (let i = 0; i < b.raw.length; i++) {
      const r = b.raw[i];
      if (r[0] - last >= STEP_MS || i === b.raw.length - 1) {
        pts.push([Math.round(r[0] / 1000), +r[1].toFixed(5), +r[2].toFixed(5), r[3]]);
        last = r[0];
      }
    }
    boats.push({ mmsi: b.mmsi, name: b.name, pts });
  }
  return json({ boats, hours: HOURS, since: new Date(since).toISOString().slice(0, 19) + "Z",
                served: now.toISOString().slice(0, 19) + "Z", files }, 120);
}

export async function onRequest({ request }) {
  if (request.method === "GET") return onRequestGet();
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
