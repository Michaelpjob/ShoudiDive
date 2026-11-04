// GET /api/fleet/vessel?mmsi=366123456&hours=48 — one hull's live track from
// the every-vessel feed, fleet boat or not: {mmsi, info, pts:[[epochSec, lat,
// lon, sog, cog], ...] oldest first, logging_since}. Points are thinned at the
// logger (one per 2 min under way, a heartbeat every 30 min stopped), so a gap
// longer than ~30 min means the hull was out of range. `info` is null for a
// hull not heard in the last week. Edge-cached reads, two minutes.
import { day, readAny, readAnyIndex, json } from "./_lib.js";

const MAX_H = 168;

export function shardOf(mmsi) { return String(Number(mmsi) % 100).padStart(2, "0"); }

export function parseShard(text, mmsi, since) {
  const pts = [], pre = mmsi + ",";
  for (const line of String(text || "").split("\n")) {
    if (!line.startsWith(pre)) continue;
    const f = line.split(",");
    const t = +f[1], lat = +f[2], lon = +f[3];
    if (!(t >= since) || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    pts.push([t, lat, lon, f[4] === "" || f[4] == null ? null : +f[4], f[5] === "" || f[5] == null ? null : +f[5]]);
  }
  return pts;
}

export async function onRequestGet({ request }) {
  const q = new URL(request.url).searchParams;
  const mmsi = (q.get("mmsi") || "").trim();
  if (!/^\d{9}$/.test(mmsi)) return json({ error: "mmsi must be 9 digits" }, 60, 400);
  const hours = Math.max(1, Math.min(MAX_H, Math.round(+q.get("hours") || 48)));
  const now = Date.now(), since = Math.floor(now / 1000) - hours * 3600;
  const dates = [];
  for (let h = hours; h >= 0; h -= 24) dates.push(day(new Date(now - h * 3600e3)));
  dates.push(day(new Date(now)));
  const files = [...new Set(dates)];
  const [idx, ...texts] = await Promise.all([readAnyIndex(), ...files.map((d) => readAny(d + "/" + shardOf(mmsi) + ".csv"))]);
  const pts = texts.flatMap((t) => parseShard(t, mmsi, since));
  pts.sort((a, b) => a[0] - b[0]);
  const x = idx && idx.v ? idx.v[mmsi] : null;
  const info = x ? { name: x.n || null, type: x.ty || null, len: x.len || null, callsign: x.cs || null, dest: x.d || null,
                     t: x.t || null, lat: x.la ?? null, lon: x.lo ?? null, sog: x.s ?? null, cog: x.c ?? null, heading: x.h ?? null } : null;
  return json({ mmsi: +mmsi, info, pts, hours, since: new Date(since * 1000).toISOString().slice(0, 19) + "Z",
                logging_since: idx ? idx.since : null, served: new Date(now).toISOString().slice(0, 19) + "Z", files }, 60);
}

export async function onRequest(ctx) {
  if (ctx.request.method === "GET") return onRequestGet(ctx);
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
