# ShouldIDive — Security policy

## Reporting a vulnerability

If you find a security issue, please report it via
[GitHub Security Advisories on this repo](https://github.com/Michaelpjob/ShoudiDive/security/advisories)
rather than opening a public issue. We'll acknowledge within 72 hours.

Do **not**:

- Run automated scanners against `shouldidive.com` (we already see
  enough of those from CN/IN/RU script-kiddie IP blocks; please
  don't add to the noise).
- Use vulnerabilities to access data beyond what's needed to
  demonstrate them.
- Modify data, deface pages, or degrade service.

## What's in scope

- The live web app at `https://shouldidive.com/` and its beta surfaces
  (`{ca,pnw,tropical}-beta.shouldidive.pages.dev`, `dev.shouldidive.pages.dev`).
- The single Cloudflare Pages Function at `/api/analytics/event`.
- The GitHub Actions workflows in `.github/workflows/` (if you find a
  way to inject into our auto-deploy chain).
- Mobile app (`mobile/`) — currently RN/Expo, single-purpose read-only client.

## What's out of scope

- The published static data under `/data/` is, by design, fully
  public. Reading it isn't a vulnerability.
- Cloudflare and NOAA upstream infrastructure.
- Denial of service via volumetric traffic — Cloudflare's free tier
  handles that. Logical-flaw DoS (e.g. an endpoint that allocates
  unbounded memory for a small request) IS in scope.
- Self-XSS that requires the victim to paste attacker-supplied JS
  into devtools.

## Security posture summary (2026-05-13)

### Surface area

- One POST endpoint, `/api/analytics/event`. Same-origin only, no CORS.
- Everything else is static (HTML, JS, CSS bundles, PNG / JSON data).
- No user accounts. No auth. No DB writes from the web tier.
- No third-party JS bundled — fonts come from `fonts.googleapis.com` /
  `fonts.gstatic.com`, no analytics or ad SDKs.

### Code-level hardening

- Zero `dangerouslySetInnerHTML`, zero `eval()`, zero `new Function()`,
  zero `innerHTML` writes, zero `document.write` in the React app.
- All URL params come through `URLSearchParams` and are whitelisted
  against known values (region switcher).
- No `import.meta.env.*` secrets in the client bundle; only `PROD`
  flag is read (build-time boolean).
- `git ls-files` has zero `.env`, `.pem`, `id_rsa`, or other
  credential files tracked.

### `/api/analytics/event` defense-in-depth

- 16 KB body cap.
- 50-events-per-batch cap.
- 64-char prop string cap.
- Strict allowlist of event names.
- Origin/Referer check against `TRUSTED_ORIGINS` whitelist.
- Content-Type filter (rejects non-JSON / non-text/plain).
- Session-ID charset restricted to `[a-zA-Z0-9_-]+`.
- IP address and User-Agent deliberately never logged.
- No CORS — cross-origin browsers can't reach the endpoint.

### Cloudflare Pages headers (see `public/_headers`)

- `Content-Security-Policy` — denies inline scripts, restricts img/font/style/connect/worker/manifest to same-origin (+ Google Fonts for style/font). `frame-ancestors 'none'` blocks clickjacking. `upgrade-insecure-requests` auto-upgrades.
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — HSTS enforced for 1 year on the domain and all subdomains. Preload submission deferred until policy stable for >2 weeks.
- `X-Frame-Options: DENY` — belt-and-suspenders alongside CSP frame-ancestors.
- `X-Content-Type-Options: nosniff` — no MIME guessing.
- `Referrer-Policy: strict-origin-when-cross-origin`.
- `Permissions-Policy` — denies camera/mic/geolocation/payment/USB/etc.; allows `geolocation=(self)` + `fullscreen=(self)` only (we don't use them today but want headroom).
- `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Resource-Policy: same-site` — isolates browsing context.

### Cloudflare Pages routing (see `public/_redirects`)

- Scanner-known paths (`/.env*`, `/.git/*`, `/wp-*`, `/phpmyadmin*`, etc.) return **hard HTTP 404** instead of the SPA fallback's 200-with-HTML. Lowers our visibility in attacker target queues.

## Recommended Cloudflare dashboard settings

The code-level work in this repo is half of the story. The other half
lives in the Cloudflare dashboard for the `shouldidive.com` zone.

If you (the repo owner) haven't already, set these:

### 1. Security → WAF → Managed rules
- Enable **Cloudflare Managed Ruleset** (free).
- Set sensitivity to **High** for the first few weeks; ratchet down if you see false positives.
- Enable **OWASP ModSecurity Core Rule Set** in monitoring mode for a week, then enforce.

### 2. Security → Bots → Bot Fight Mode
- Enable **Bot Fight Mode** (free; auto-challenges known-bot IPs).
- Don't enable Super Bot Fight Mode unless you're on Pro plan — it costs more and the free tier is usually enough.

### 3. Security → Rate Limiting → Rate limiting rules
Add a rule scoped to the analytics endpoint:
- **Rule name:** `analytics-rate-limit`
- **Match:** `http.request.uri.path eq "/api/analytics/event" and http.request.method eq "POST"`
- **Rate:** 20 requests / 10 seconds, per IP.
- **Action:** Block, duration 1 minute.

(Free tier includes one rate-limiting rule.)

### 4. SSL/TLS → Edge Certificates
- Enforce **Always Use HTTPS**.
- **Min TLS Version:** 1.2 (or 1.3 if your audience is modern).
- **Automatic HTTPS Rewrites:** on.
- **HSTS:** the `_headers` file already declares it; you don't need to also enable Cloudflare's HSTS plugin (would cause duplicate headers).

### 5. Speed → Optimization → Auto Minify
- Skip. Vite already minifies; CF's pass after that has no benefit and can break source maps if we ever enable them.

### 6. Security → Settings → Browser Integrity Check
- Enable.

### 7. (Optional, paid) Cloudflare Turnstile
- Embed Turnstile on `/api/analytics/event` if bot floods continue
  past WAF + Bot Fight Mode + rate limiting. Free for the first 1M
  monthly challenges.

### Geographic posture

The user has reported elevated bot traffic from CN/IN/RU IP ranges.

- **Don't geo-block.** Many legitimate divers travel; blocking
  countries hurts real users disproportionately.
- The right move is the combination above: WAF + Bot Fight + rate
  limit + hard-404s. Together those drop scanner success rate to
  near zero without false positives.
- If a specific ASN or IP range becomes consistently malicious,
  add a per-ASN block under Security → WAF → Custom rules.

## Maintenance / review cadence

- Re-run `_redirects` against new scanner patterns whenever CF
  Analytics shows a new path class spiking (~quarterly).
- Re-audit the analytics endpoint when `ANALYTICS_KV` Phase 2 lands
  (storage abuse becomes a real cost; tighten rate limit then).
- Rotate any CF API tokens annually and on any secret-leak suspicion.
