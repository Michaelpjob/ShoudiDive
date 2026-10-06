// GET /api/fleet/now — the fleet's latest live AIS positions: the last point
// per MMSI from today's and yesterday's live day files (hulls silent longer
// than MAX_AGE_H drop off). Same-origin so the tool's strict CSP
// (connect-src 'self') is satisfied; edge-cached for two minutes.
import { readWindow, epoch, json } from "./_lib.js";

const MAX_AGE_H = 36;

export async function onRequestGet() {
  const now = new Date();
  const { rows, files } = await readWindow(now, 24);
  const last = new Map();
  for (const p of rows) {
    if (!p.mmsi || p.latitude == null || p.longitude == null) continue;
    const t = epoch(p);
    if (t == null) continue;
    const prev = last.get(p.mmsi);
    if (!prev || t > prev.t) last.set(p.mmsi, { t, p });
  }
  const cutoff = now.getTime() - MAX_AGE_H * 3600e3;
  const boats = [];
  for (const { t, p } of last.values()) {
    if (t < cutoff) continue;
    boats.push({
      mmsi: p.mmsi, name: p.vessel_name || null, lat: p.latitude, lon: p.longitude,
      sog: p.sog == null ? null : Number(p.sog), cog: p.cog == null ? null : Number(p.cog),
      heading: p.heading === 511 ? null : p.heading, t: new Date(t).toISOString().slice(0, 19) + "Z",
    });
  }
  boats.sort((a, b) => (a.t < b.t ? 1 : -1));
  return json({ boats, freshest: boats.length ? boats[0].t : null, served: now.toISOString().slice(0, 19) + "Z", files }, 120);
}

export async function onRequest({ request }) {
  if (request.method === "GET") return onRequestGet();
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
