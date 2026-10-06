// Shared readers for the live-fleet endpoints. The live logger commits
// roster-boat positions to the public `fleet-live` branch as
// live/<UTC date>.jsonl; we read them straight from raw.githubusercontent.com
// (public repo, no token) with a short edge cache.
const RAW = "https://raw.githubusercontent.com/Michaelpjob/ShoudiDive/fleet-live/live/";

export function day(d) { return d.toISOString().slice(0, 10); }

export async function readDay(date) {
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

export function epoch(p) {
  const t = Date.parse(String(p.base_date_time).replace(" ", "T") + "Z");
  return Number.isFinite(t) ? t : null;
}

export async function readWindow(now, hours) {
  const dates = [];
  for (let h = hours; h >= 0; h -= 24) dates.push(day(new Date(now.getTime() - h * 3600e3)));
  dates.push(day(now));
  const uniq = [...new Set(dates)];
  const rows = (await Promise.all(uniq.map(readDay))).flat();
  return { rows, files: uniq };
}

export function json(body, maxAge) {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=" + maxAge },
  });
}
