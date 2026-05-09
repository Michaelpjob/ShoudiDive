// Privacy-respecting in-app analytics.
//
// What this is:
//   * A tiny event tracker that lets the React app emit named events
//     ("layer_change", "spot_click", "settings_change", ...) with a
//     small properties payload.
//   * A buffered POST sender that batches events and ships them to
//     /api/analytics/event (a Cloudflare Pages Function — see
//     functions/api/analytics/event.js) every 30s and on tab close.
//
// What this is NOT:
//   * No cookies. Each session generates a random 64-bit ID held in
//     sessionStorage; it lives only until the tab closes.
//   * No third-party trackers. All events go to our own /api endpoint
//     served from the same origin (no CORS, no leakage).
//   * No PII. We send: event name, named props, the layer the user
//     was viewing, viewport size class (mobile/desktop), local UTC
//     hour, and the random session ID. We don't send IP, user-agent,
//     coordinates, or saved-spot identity beyond what the user's
//     current click implies.
//
// What you can ask of the data:
//   * "How many sessions per day?" — count distinct session_id
//   * "Most-viewed layer?" — count layer_change events grouped by `to`
//   * "Are people actually clicking the SST forecast toggle?" —
//     count sst_mode_change events
//   * "Is mobile-vs-desktop usage 50/50 or 90/10?" — group by viewport
//
// Phase 1 (this file): events are just logged by the Pages Function.
// You read them in the Cloudflare dashboard's Real-time Logs tab.
// Phase 2 (follow-up): events get persisted to a Cloudflare KV
// namespace + aggregated nightly into a JSON published at /stats.

const ENDPOINT = "/api/analytics/event";
const FLUSH_INTERVAL_MS = 30_000;
const MAX_BUFFER = 25;

// 64-bit random ID; sessionStorage keeps it alive across page reloads
// within the same tab but expires when the tab closes. Different tab =
// different session, which is the GA convention.
function ensureSessionId() {
  if (typeof window === "undefined") return null;
  try {
    let id = window.sessionStorage.getItem("sd:sid");
    if (id) return id;
    const buf = new Uint8Array(8);
    (window.crypto || {}).getRandomValues?.(buf);
    id = Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
    window.sessionStorage.setItem("sd:sid", id);
    return id;
  } catch {
    // Private mode / storage disabled — we still want events to flow.
    return "no-session";
  }
}

function viewportClass() {
  if (typeof window === "undefined") return "unknown";
  const w = window.innerWidth || 0;
  if (w < 600) return "mobile";
  if (w < 1024) return "tablet";
  return "desktop";
}

function pad(n) {
  return n < 10 ? "0" + n : "" + n;
}

function nowIsoMinute() {
  // Truncate to the minute. Cardinality of timestamps blows up the
  // back-end aggregation cost otherwise. Minute-level is plenty for
  // usage analytics; we don't need second-level.
  const d = new Date();
  return (
    d.getUTCFullYear() +
    "-" +
    pad(d.getUTCMonth() + 1) +
    "-" +
    pad(d.getUTCDate()) +
    "T" +
    pad(d.getUTCHours()) +
    ":" +
    pad(d.getUTCMinutes()) +
    "Z"
  );
}

const _buffer = [];
let _flushTimer = null;
let _started = false;
let _disabled = false;

function send(payload) {
  // sendBeacon is the only reliable way to ship events on tab close.
  // It survives navigation away from the page where fetch() would
  // be aborted. Cloudflare Pages Functions accept it as a normal POST.
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([JSON.stringify(payload)], {
        type: "application/json",
      });
      const ok = navigator.sendBeacon(ENDPOINT, blob);
      if (ok) return true;
    }
  } catch {
    // fall through to fetch
  }
  try {
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
    return true;
  } catch {
    return false;
  }
}

function flush() {
  if (_disabled) return;
  if (_buffer.length === 0) return;
  const events = _buffer.splice(0, _buffer.length);
  send({
    session_id: ensureSessionId(),
    viewport: viewportClass(),
    sent_at: nowIsoMinute(),
    events,
  });
}

/**
 * Fire-and-forget event tracker. Buffered + batched.
 *
 * @param {string} name e.g. "layer_change", "spot_click", "settings_change"
 * @param {object} [props] flat object of safe primitives only
 */
export function track(name, props) {
  if (_disabled || typeof window === "undefined") return;
  if (!name) return;
  // Defensive: only ship JSON-safe primitives. Drop functions, DOM
  // nodes, etc. that callers might accidentally pass.
  const safeProps = {};
  if (props && typeof props === "object") {
    for (const [k, v] of Object.entries(props)) {
      const t = typeof v;
      if (v === null || t === "string" || t === "number" || t === "boolean") {
        safeProps[k] = v;
      }
    }
  }
  _buffer.push({
    name,
    ts: nowIsoMinute(),
    props: safeProps,
  });
  if (_buffer.length >= MAX_BUFFER) flush();
}

/**
 * Disable analytics for the rest of this page session. Honors
 * Do-Not-Track and any user-set localStorage opt-out — call this
 * from a settings UI if you build one.
 */
export function disableAnalytics() {
  _disabled = true;
  _buffer.length = 0;
}

/**
 * Initialize buffered flush + page-load event. Call once from main.jsx.
 */
export function initAnalytics() {
  if (_started || typeof window === "undefined") return;
  _started = true;

  // Honor the browser's Do-Not-Track signal. Privacy-respecting by
  // default; users who set DNT get no telemetry, period.
  if (window.navigator && window.navigator.doNotTrack === "1") {
    _disabled = true;
    return;
  }
  // Honor a localStorage opt-out (so you can give users a UI later
  // without having to re-add machinery).
  try {
    if (window.localStorage.getItem("sd:analytics:off") === "1") {
      _disabled = true;
      return;
    }
  } catch {
    /* ignore */
  }

  // Initial pageview — fires once per tab. Subsequent navigations
  // (none on a single-page app) would fire more.
  track("pageview", {
    path: window.location.pathname,
    referrer: document.referrer ? new URL(document.referrer).host : "",
    viewport: viewportClass(),
  });

  // Periodic flush so events ship even on long sessions where the
  // user never navigates away.
  _flushTimer = window.setInterval(flush, FLUSH_INTERVAL_MS);

  // Ship buffered events on tab close / hide. visibilitychange covers
  // mobile (where pagehide fires) AND desktop tab-close.
  const onHide = () => flush();
  window.addEventListener("visibilitychange", onHide);
  window.addEventListener("pagehide", onHide);
  window.addEventListener("beforeunload", onHide);
}
