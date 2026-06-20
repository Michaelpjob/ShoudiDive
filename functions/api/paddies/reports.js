// GET /api/paddies/reports — public list of APPROVED reports for the map, each
// tagged with a (non-PII) trust tier from corroboration + reporter reputation.
import { jsonResponse, readApproved, listEmails, buildRepMap, attachConfidence, publicView } from "./_lib.js";

export async function onRequestGet({ env }) {
  if (!env || !env.REPORTS_KV) return jsonResponse({ reports: [] });
  const nowMs = Date.now();
  const approved = await readApproved(env, nowMs);
  const repMap = buildRepMap(await listEmails(env));
  const withConf = attachConfidence(approved, repMap);
  return jsonResponse(
    { reports: withConf.map(publicView), updated: nowMs },
    { cache: "public, max-age=60" } // approvals show within ~a minute
  );
}

export async function onRequest({ request, env }) {
  if (request.method === "GET") return onRequestGet({ env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
}
