// GET /api/paddies/reports — public list of APPROVED reports for the map.
// Single fast read of the approved blob; short edge cache so the map doesn't hammer KV.
import { jsonResponse, readApproved, publicView } from "./_lib.js";

export async function onRequestGet({ env }) {
  if (!env || !env.REPORTS_KV) return jsonResponse({ reports: [] });
  const nowMs = Date.now();
  const arr = await readApproved(env, nowMs);
  return jsonResponse(
    { reports: arr.map(publicView), updated: nowMs },
    { cache: "public, max-age=60" } // approvals show within ~a minute
  );
}

export async function onRequest({ request, env }) {
  if (request.method === "GET") return onRequestGet({ env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
