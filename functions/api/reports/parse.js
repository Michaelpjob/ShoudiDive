// POST /api/reports/parse {text}: read a report written as a sentence into
// report fields, for the page to fill its form with. Stores nothing.
import { jsonResponse } from "./_lib.js";
import { toSubmission } from "./_parse.js";

export async function onRequestPost({ request }) {
  let body;
  try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "bad json" }, { status: 400 }); }
  const text = String((body || {}).text || "").slice(0, 1000);
  if (text.trim().length < 6) return jsonResponse({ ok: false, error: "write a few words about what you caught or saw" }, { status: 422 });
  const sent = Date.parse(String((body || {}).now || "")) || Date.now();
  const { body: draft, parsed } = toSubmission(text, sent);
  const missing = [];
  if (!draft.type) missing.push("the fish or what you saw");
  if (draft.lat == null) missing.push("where (a GPS number or a spot name)");
  return jsonResponse({ ok: true, draft, parsed, missing });
}

export async function onRequest({ request }) {
  if (request.method === "POST") return onRequestPost({ request });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "POST" } });
}
