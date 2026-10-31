// Reports that arrive as words: an email to the reports address or a text
// message. Both go through the same reader as the page's sentence box and land
// in the same pending queue; nothing shows until a moderator approves it.
import { validate, rateOk, sha256hex, storePending } from "./_lib.js";
import { toSubmission } from "./_parse.js";

export const HELP = "Include the fish and where: a GPS number (32 52 x 117 17) or a spot name (9 Mile, Coronados, the 302).";

// Returns {ok, id} or {ok:false, error}. `who` = {email (or "text:+1..."), name}.
export async function inboundReport(env, text, who, via, nowMs = Date.now()) {
  const clean = String(text || "").slice(0, 2000);
  const key = (await sha256hex(String(who.email).toLowerCase())).slice(0, 16);
  if (!(await rateOk(env, `${via}:${key}`, 10, 86400))) return { ok: false, error: "Too many reports from this sender today." };
  const { body } = toSubmission(clean, nowMs);
  if (!body.type || body.lat == null) return { ok: false, error: "We couldn't read " + (!body.type ? "a fish or a sighting" : "a place") + ". " + HELP };
  const v = validate({ ...body, via }, nowMs);
  if (v.error) return { ok: false, error: v.error + ". " + HELP };
  const id = await storePending(env, v.rec, { email: who.email, name: who.name || "", deviceId: via, ipHash: "", raw_text: clean }, nowMs);
  return { ok: true, id };
}

// --- Email: the text part of a MIME message ------------------------------------
function unfold(head) { return head.replace(/\r?\n[ \t]+/g, " "); }
function header(head, name) {
  const want = name.toLowerCase() + ":";
  const line = unfold(head).split(/\r?\n/).find((l) => l.toLowerCase().startsWith(want));
  return line ? line.slice(want.length).trim() : "";
}
function decodeBody(body, enc) {
  enc = String(enc || "").toLowerCase();
  if (enc === "base64") {
    try {
      const bin = atob(body.replace(/\s+/g, ""));
      return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
    } catch { return ""; }
  }
  if (enc === "quoted-printable") {
    const bytes = [];
    const s = body.replace(/=\r?\n/g, "");
    for (let i = 0; i < s.length; i++) {
      if (s[i] === "=" && /^[0-9A-F]{2}$/i.test(s.slice(i + 1, i + 3))) { bytes.push(parseInt(s.slice(i + 1, i + 3), 16)); i += 2; }
      else bytes.push(s.charCodeAt(i) & 0xff);
    }
    return new TextDecoder().decode(new Uint8Array(bytes));
  }
  return body;
}
function stripHtml(h) {
  return h.replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, " ").replace(/<br\s*\/?>|<\/p>/gi, "\n").replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
}
// The message's own words: the first text/plain part (or text/html, stripped),
// without quoted replies or the signature.
export function mimeText(raw) {
  const split = String(raw).search(/\r?\n\r?\n/);
  if (split < 0) return "";
  const head = raw.slice(0, split), body = raw.slice(split).replace(/^\r?\n\r?\n/, "");
  const ct = header(head, "Content-Type") || "text/plain";
  const boundary = /boundary="?([^";]+)"?/i.exec(ct);
  let text = "";
  if (/^multipart\//i.test(ct) && boundary) {
    const parts = body.split("--" + boundary[1]).slice(1).filter((p) => !/^--/.test(p));
    const texts = parts.map((p) => ({ p: p.replace(/^\r?\n/, ""), ct: header(p.replace(/^\r?\n/, "").split(/\r?\n\r?\n/)[0], "Content-Type") }));
    const plain = texts.find((x) => /^text\/plain/i.test(x.ct)) || texts.find((x) => /^multipart\//i.test(x.ct)) || texts.find((x) => /^text\/html/i.test(x.ct));
    return plain ? mimeText(plain.p) : "";
  }
  text = decodeBody(body, header(head, "Content-Transfer-Encoding"));
  if (/^text\/html/i.test(ct)) text = stripHtml(text);
  const lines = text.split(/\r?\n/);
  const out = [];
  for (const ln of lines) {
    if (/^-- ?$/.test(ln) || /^On .+ wrote:$/.test(ln.trim()) || /^Sent from my /i.test(ln.trim())) break;
    if (!/^\s*>/.test(ln)) out.push(ln);
  }
  return out.join("\n").trim();
}
export function fromHeader(raw) {
  const split = String(raw).search(/\r?\n\r?\n/);
  const v = header(split < 0 ? raw : raw.slice(0, split), "From");
  const m = /^\s*"?([^"<]*?)"?\s*<([^>]+)>/.exec(v);
  return m ? { name: m[1].trim(), email: m[2].trim() } : { name: "", email: v.trim() };
}

// --- Text messages: Twilio's webhook signature -----------------------------------
// X-Twilio-Signature = base64(HMAC-SHA1(auth token, URL + each POST param name
// and value, sorted by name)).
export async function twilioSignatureOk(authToken, url, params, signature) {
  if (!authToken || !signature) return false;
  const data = url + Object.keys(params).sort().map((k) => k + params[k]).join("");
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(authToken), { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data)));
  const expected = btoa(String.fromCharCode(...mac));
  if (expected.length !== signature.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  return diff === 0;
}
