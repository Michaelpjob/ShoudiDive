// Shared helpers for the analytics dashboard endpoints (summary + stats).
//
// This file has NO onRequest* export, so Pages doesn't treat it as a
// route — it's just a module the route files import. The leading-underscore
// name is belt-and-suspenders (Pages also won't route a handler-less file).
//
// Counting model: src/lib/analytics.js → /api/analytics/event writes ONE KV
// key per event, shaped `ev/<day>/<name>/<session>/<uuid>`. The event name
// and session id live in the KEY, so we tally totals AND unique sessions
// from a key-list scan alone — no per-value reads.

// Token gate. Returns null when allowed, else a short reason string.
// Fails CLOSED: if STATS_TOKEN isn't configured, access is refused.
export function gate(request, env) {
  const expected = env && env.STATS_TOKEN;
  if (!expected) return "unconfigured";
  const url = new URL(request.url);
  const token = url.searchParams.get("key") || request.headers.get("x-stats-token") || "";
  if (token.length !== expected.length || token !== expected) return "denied";
  return null;
}

function isoDay(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

// The last `days` UTC dates, newest first.
export function recentDays(days) {
  const today = Date.now();
  const out = [];
  for (let i = 0; i < days; i++) out.push(isoDay(today - i * 86400000));
  return out;
}

// Scan one day's keys → { total: {name:n}, sids: {name:Set} }.
async function scanDay(env, day) {
  const prefix = `ev/${day}/`;
  const total = {};
  const sids = {};
  let cursor;
  do {
    const res = await env.ANALYTICS_KV.list({ prefix, cursor, limit: 1000 });
    for (const k of res.keys) {
      const p = k.name.split("/"); // [ev, day, name, session, uuid]
      const name = p[2];
      const sid = p[3] || "";
      if (!name) continue;
      total[name] = (total[name] || 0) + 1;
      (sids[name] || (sids[name] = new Set())).add(sid);
    }
    cursor = res.list_complete ? null : res.cursor;
  } while (cursor);
  return { total, sids };
}

// Aggregate the last `days` days. Returns:
//   { days: [{ date, events: { name: {total, unique} } }],  // newest first
//     totals: { name: {total, unique} } }                   // unique = over window
export async function aggregate(env, days) {
  const list = recentDays(days);
  const perDay = [];
  const totalByName = {};
  const sidByName = {}; // union of sessions across the whole window
  for (const date of list) {
    const { total, sids } = await scanDay(env, date);
    const events = {};
    for (const name of Object.keys(total)) {
      events[name] = { total: total[name], unique: sids[name].size };
      totalByName[name] = (totalByName[name] || 0) + total[name];
      if (!sidByName[name]) sidByName[name] = new Set();
      for (const s of sids[name]) sidByName[name].add(s);
    }
    perDay.push({ date, events });
  }
  const totals = {};
  for (const name of Object.keys(totalByName)) {
    totals[name] = { total: totalByName[name], unique: sidByName[name].size };
  }
  return { days: perDay, totals };
}
