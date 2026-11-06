// POST /api/reports/sms: Twilio's incoming-message webhook. A text like
// "12 yellowtail at the 9 Mile on sardines" becomes a pending report; the reply
// says it is in the queue, or what to add. Inert until TWILIO_AUTH_TOKEN is
// set and a Twilio number points its messaging webhook here.
import { inboundReport, twilioSignatureOk } from "./_inbound.js";

function twiml(msg, status = 200) {
  const x = msg.replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><Response><Message>${x}</Message></Response>`,
    { status, headers: { "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-store" } });
}

export async function onRequestPost({ request, env }) {
  if (!env || !env.REPORTS_KV || !env.TWILIO_AUTH_TOKEN) return new Response("text reports are not set up", { status: 503 });
  const form = await request.formData();
  const params = {};
  for (const [k, v] of form.entries()) params[k] = String(v);
  const url = env.TWILIO_WEBHOOK_URL || request.url;
  if (!(await twilioSignatureOk(env.TWILIO_AUTH_TOKEN, url, params, request.headers.get("X-Twilio-Signature")))) {
    return new Response("bad signature", { status: 403 });
  }
  const from = String(params.From || "").replace(/[^\d+]/g, "");
  if (!from) return twiml("We couldn't tell who sent this.");
  const r = await inboundReport(env, params.Body || "", { email: "text:" + from, name: "" }, "text");
  return twiml(r.ok ? "Thanks. Your report is in the review queue; it shows on shouldidive.com/reports once approved." : r.error);
}

export async function onRequest({ request, env }) {
  if (request.method === "POST") return onRequestPost({ request, env });
  return new Response("Method Not Allowed", { status: 405, headers: { Allow: "POST" } });
}
