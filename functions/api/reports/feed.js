// GET /api/reports/feed: approved field reports from the last three weeks,
// without any reporter details, each with a corroboration tier.
import { jsonResponse, readApproved, listEmails, repScore, attachConfidence, publicView } from "./_lib.js";

export async function onRequestGet({ env }) {
  if (!env || !env.REPORTS_KV) return jsonResponse({ reports: [] });
  const nowMs = Date.now();
  const approved = await readApproved(env, nowMs);
  const repMap = {};
  for (const e of await listEmails(env)) if (e.email) repMap[e.email.toLowerCase()] = repScore(e);
  const out = attachConfidence(approved, repMap).map(publicView).sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  return jsonResponse({ reports: out, updated: nowMs }, { cache: "public, max-age=60" });
}

export async function onRequest({ request, env }) {
  if (request.method === "GET") return onRequestGet({ env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
