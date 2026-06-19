// Cloudflare Pages Function — analytics event ingest endpoint.
//
// Receives batched events from src/lib/analytics.js and:
//   * Validates the payload shape (rejects oversize / malformed)
//   * Adds server-side timestamp + a privacy-preserving country tag
//   * Logs each event to console (visible in the Cloudflare Pages
//     dashboard's Real-time Logs tab — that's the Phase 1 storage)
//
// Phase 2 (follow-up):
//   * Bind a Cloudflare KV namespace as `ANALYTICS_KV` and persist
//     events keyed by `{date}/{session_id}/{seq}` so a separate
//     /api/analytics/summary endpoint can aggregate.
//   * Add a /stats page that reads the aggregated JSON and renders
//     the dashboard.
//
// Privacy notes:
//   * No IP address logged. Cloudflare gives us the request IP via
//     `request.headers.get("CF-Connecting-IP")` but we deliberately
//     ignore it. The country header is fine — it's already
//     anonymized by CF and useful for "where are users from?".
//   * No User-Agent logged for the same reason.
//   * Session ID is the random 64-bit token the client minted; it
//     dies when the user closes the tab.
//
// Routing:
//   This file lives at functions/api/analytics/event.js, which
//   Cloudflare Pages Functions auto-mounts at /api/analytics/event.
//   No wrangler.toml route config needed.

const MAX_BODY_BYTES = 16 * 1024;       // 16 KB cap; honest clients send <1 KB
const MAX_EVENTS_PER_BATCH = 50;        // matches client buffer size + headroom

// Origin allowlist. Same-origin requests from shouldidive.com don't
// always set Origin/Referer (depends on browser + sendBeacon mode),
// but when set they must be one of these. Listed verbatim rather than
// regex to keep the check trivially auditable.
const TRUSTED_ORIGINS = new Set([
  "https://shouldidive.com",
  "https://www.shouldidive.com",
  "https://dev.shouldidive.pages.dev",
  "https://ca-beta.shouldidive.pages.dev",
  "https://pnw-beta.shouldidive.pages.dev",
  "https://tropical-beta.shouldidive.pages.dev",
  // shouldidive.pages.dev — root Pages preview that CF auto-generates
  "https://shouldidive.pages.dev",
]);

function isTrustedOrigin(probe) {
  try {
    const u = new URL(probe);
    return TRUSTED_ORIGINS.has(`${u.protocol}//${u.host}`);
  } catch {
    return false;
  }
}

const ALLOWED_NAMES = new Set([
  "pageview",
  "layer_change",
  "sst_mode_change",
  "spot_click",
  "settings_change",
  "tooltip_open",
  "popup_open",
  "timeline_drag",
  "zoom",
  "share_click",
  "tip_click",
  "whatsnew_open",
  "column_open",
  "column_depth_set",
]);

function jsonResponse(body, init) {
  return new Response(JSON.stringify(body), {
    status: (init && init.status) || 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      // No CORS — same-origin only by design. Don't set
      // Access-Control-Allow-Origin; we want browsers to refuse
      // cross-origin POSTs to this endpoint.
    },
  });
}

export async function onRequestPost({ request }) {
  // ---- defense-in-depth guard rails ---------------------------------
  // Each of these checks is cheap (header read or constant-time string
  // compare) and short-circuits before we spend any CPU on body parsing.
  // The intent isn't perfect rejection — it's making this endpoint
  // unattractive to opportunistic abuse:
  //   * a flood of bot POSTs adds noise to Cloudflare logs (Phase 1
  //     storage); harmless today but degrades the signal we built
  //     analytics for
  //   * Phase 2 (when ANALYTICS_KV is bound) every successful POST
  //     writes a KV entry; abuse becomes a real $ cost
  //   * Rate-limit / WAF rules at the Cloudflare dashboard are the
  //     primary defense; the checks below are belt-and-suspenders
  //     so the endpoint still behaves correctly if a config drifts

  // Origin / Referer check. Honest browser clients always send Origin
  // for `fetch()` POST requests and either Origin OR Referer for
  // sendBeacon. Reject anything that's missing both (script tools,
  // curl-from-CLI without --header). Allow same-origin and our beta
  // surfaces only.
  const origin = request.headers.get("origin") || "";
  const referer = request.headers.get("referer") || "";
  if (origin || referer) {
    const probe = origin || referer;
    if (!isTrustedOrigin(probe)) {
      return jsonResponse({ ok: false, error: "untrusted origin" }, { status: 403 });
    }
  }
  // (Missing-both case allowed — sendBeacon under some privacy modes
  // strips the headers; we don't want to reject legitimate beacons.)

  // Content-Type must be JSON. Bots that just blindly POST a form
  // encoded body trip on this immediately.
  const contentType = (request.headers.get("content-type") || "").toLowerCase();
  if (contentType && !contentType.startsWith("application/json") && !contentType.startsWith("text/plain")) {
    // text/plain is what sendBeacon sometimes sets when the body is
    // a string. We accept it because the body parser doesn't care.
    return jsonResponse({ ok: false, error: "bad content-type" }, { status: 415 });
  }

  // Defensive size guard. sendBeacon will happily POST a 10 MB blob
  // if the client buffer is broken; we never want to spend a full
  // request CPU budget on a malformed payload.
  const contentLength = parseInt(request.headers.get("content-length") || "0", 10);
  if (contentLength > MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "payload too large" }, { status: 413 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid json" }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return jsonResponse({ ok: false, error: "expected object" }, { status: 400 });
  }

  const sessionId = String(body.session_id || "").slice(0, 32);
  const viewport = String(body.viewport || "").slice(0, 16);
  const sentAt = String(body.sent_at || "").slice(0, 32);
  const events = Array.isArray(body.events) ? body.events : [];

  if (!sessionId || events.length === 0) {
    return jsonResponse({ ok: false, error: "missing session or events" }, { status: 400 });
  }
  if (events.length > MAX_EVENTS_PER_BATCH) {
    return jsonResponse({ ok: false, error: "too many events" }, { status: 413 });
  }
  // Session ID must be ASCII hex-ish (the client mints it with
  // crypto.getRandomValues → toString(16)). This catches the
  // "bot copy-paste of a session ID with `<` or `'` in it" cases
  // that are otherwise harmless but signal abuse.
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    return jsonResponse({ ok: false, error: "bad session id" }, { status: 400 });
  }

  // Cloudflare's request object exposes the country code under cf
  // (no IP, no fingerprint). Useful + anonymous.
  const country = (request.cf && request.cf.country) || "??";
  const receivedAt = new Date().toISOString();

  // Validate + sanitize each event; drop rather than reject on
  // per-event issues — a single malformed event from a buggy client
  // shouldn't kill the rest of the batch.
  const accepted = [];
  for (const ev of events) {
    if (!ev || typeof ev !== "object") continue;
    const name = String(ev.name || "");
    if (!ALLOWED_NAMES.has(name)) continue;
    const ts = String(ev.ts || "").slice(0, 32);
    const props = {};
    if (ev.props && typeof ev.props === "object") {
      for (const [k, v] of Object.entries(ev.props)) {
        if (k.length > 32) continue;
        const t = typeof v;
        if (v === null || t === "boolean" || t === "number") {
          props[k] = v;
        } else if (t === "string") {
          props[k] = v.slice(0, 64);
        }
      }
    }
    accepted.push({ name, ts, props });
  }

  // Phase 1 storage: console.log per event. The Cloudflare Pages
  // dashboard's Real-time Logs tab streams these, so the user can
  // watch live usage. They're also captured in Workers Logpush if
  // the user enables it (~$0.05 per million records).
  for (const ev of accepted) {
    // Compact one-line JSON per event so log greps are easy.
    // eslint-disable-next-line no-console
    console.log(
      "ANALYTICS",
      JSON.stringify({
        sid: sessionId,
        cc: country,
        vp: viewport,
        sent_at: sentAt,
        recv_at: receivedAt,
        name: ev.name,
        ts: ev.ts,
        props: ev.props,
      })
    );
  }

  // Phase 2 hook: when ANALYTICS_KV is bound, also persist.
  // (Comment out for Phase 1 — uncomment + bind the namespace
  // when you're ready to add the dashboard.)
  // const env = arguments[0].env;
  // if (env && env.ANALYTICS_KV) {
  //   const day = receivedAt.slice(0, 10);          // YYYY-MM-DD bucket
  //   const key = `${day}/${sessionId}/${Date.now()}`;
  //   await env.ANALYTICS_KV.put(key, JSON.stringify(accepted), {
  //     expirationTtl: 60 * 60 * 24 * 90,           // 90-day retention
  //   });
  // }

  return jsonResponse({ ok: true, accepted: accepted.length });
}

// Reject anything that's not a POST so the endpoint surface is tiny.
export async function onRequest({ request }) {
  if (request.method === "POST") return onRequestPost({ request });
  if (request.method === "OPTIONS") {
    // No CORS; reject preflight. Same-origin only.
    return new Response(null, { status: 405, headers: { Allow: "POST" } });
  }
  return new Response("Method Not Allowed", {
    status: 405,
    headers: { Allow: "POST" },
  });
}
