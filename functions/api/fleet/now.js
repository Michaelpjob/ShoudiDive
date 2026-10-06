// GET /api/fleet/now — the fleet's latest live AIS positions.
//
// The live logger (.github/workflows/fleet-live-logger.yml) commits roster-boat
// positions to the public `fleet-live` branch every ~10 minutes as
// live/<UTC date>.jsonl. This function reads today's and yesterday's files
// straight from raw.githubusercontent.com (public repo, no token), keeps the
// last position per MMSI, and answers same-origin so the tool's strict CSP
// (connect-src 'self') is satisfied. Edge-cached for two minutes.
const RAW = "https://raw.githubusercontent.com/Michaelpjob/ShoudiDive/fleet-live/live/";
const MAX_AGE_H = 36;          // drop hulls silent longer than this

function day(d) { return d.toISOString().slice(0, 10); }

async function readDay(date) {
  const r = await fetch(RAW + date + ".jsonl", { cf: { cacheTtl: 120, cacheEverything: true } });
  if (!r.ok) return [];
  const text = await r.text();
  const out = [];
  for (const line of text.split("\n")) {
    if (!line) continue;
    try { out.push(JSON.parse(line)); } catch { /* partial last line while the logger writes */ }
  }
  return out;
}

export async function onRequestGet() {
  const now = new Date();
  const dates = [day(new Date(now.getTime() - 86400e3)), day(now)];
  const rows = (await Promise.all(dates.map(readDay))).flat();
  const last = new Map();
  for (const p of rows) {
    if (!p.mmsi || p.latitude == null || p.longitude == null) continue;
    const t = Date.parse(String(p.base_date_time).replace(" ", "T") + "Z");
    if (!Number.isFinite(t)) continue;
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
  const freshest = boats.length ? boats[0].t : null;
  return new Response(JSON.stringify({ boats, freshest, served: now.toISOString().slice(0, 19) + "Z", files: dates }), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=120" },
  });
}

export async function onRequest({ request }) {
  if (request.method === "GET") return onRequestGet();
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
