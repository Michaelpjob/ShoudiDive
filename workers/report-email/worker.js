// shouldidive-report-email: Cloudflare Email Routing hands each message sent to
// the reports address to this Worker. The text is read with the same rules as
// the Ocean Report's sentence box (functions/api/reports/_parse.js) and lands
// in the same pending queue; a moderator approves it at /reports/review.
// A message we can't place is rejected with a note on what to include, which
// the sender gets back as a bounce.
import { inboundReport, mimeText, fromHeader } from "../../functions/api/reports/_inbound.js";

export async function handleEmail(message, env, nowMs = Date.now()) {
  const raw = await new Response(message.raw).text();
  const text = mimeText(raw);
  const from = fromHeader(raw);
  const who = { email: (message.from || from.email || "").toLowerCase(), name: from.name };
  const subject = (/^Subject:\s*(.*)$/im.exec(raw.split(/\r?\n\r?\n/)[0]) || [])[1] || "";
  const r = await inboundReport(env, [subject, text].filter(Boolean).join("\n"), who, "email", nowMs);
  if (!r.ok) message.setReject(r.error);
  return r;
}

export default {
  async email(message, env) {
    await handleEmail(message, env);
  },
};
