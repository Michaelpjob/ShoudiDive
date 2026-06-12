# Notification routing for ShoudiDive

GitHub email notifications are scoped to **only the actionable subset**.
The architecture is documented here so future contributors / agents can
follow the same pattern.

## Three-layer alerting model

```
LAYER 1 — Per-workflow resilience
  • actions/checkout wrapped in retry (handles GitHub 403 flakes)
  • wrangler pages deploy in 3-attempt loop (handles CF 5xx)
  • live-cp-manifest sleeps 90s before first probe (handles CDN warmup)

LAYER 2 — Alert router (.github/workflows/alert-router.yml)
  • Runs every 15 min
  • Classifies recent failures into severity tiers (P0/P1/P2/P3)
  • Opens/updates ONE rolling Issue per (workflow, severity)
  • Closes Issues when workflows recover (2 successes, 0 failures in 2 h)
  • Labels: severity:p0, severity:p1, severity:p2, severity:p3,
            system:alert-router, system:alert-router-heartbeat

LAYER 3 — GitHub notification subscription (account-level)
  • Email-on only severity:p0 and severity:p1 labels
  • Watch the repo "Custom" → check "Issues" with label filters
  • Everything else (P2/P3, scheduled cron noise, individual workflow
    runs) stays in-app only — no email
```

## Severity tier definitions

| Tier | Trigger | Notification |
|------|---------|--------------|
| **P0** | prod broken (shouldidive.com 5xx; manifest stale > 24 h) — `deploy-prod` failure × 1+, or `Verify live deploy` × 3+ | EMAIL |
| **P1** | dev-checks failing × 2+ in 2 h on an active branch | EMAIL |
| **P2** | sustained failure: 3+ consecutive runs of same workflow, OR `Live data health check` × 2+, OR beta-deploys × 5+ in 2 h | IN-APP ONLY |
| **P3** | single transient flake, scheduled cron jitter, single beta-deploy fail | LOGGED, NO NOTIFY |

## How to configure your inbox

1. **Mute repo-wide workflow notifications** that bypass labels:
   - GitHub avatar → **Settings → Notifications**
   - Under **Actions**: uncheck "Failed workflows only" if you have it on.
     (Trust the alert-router to surface what matters.)

2. **Subscribe to the repo with label filters**:
   - Visit the repo → **Watch** → **Custom**
   - Check only: **Issues**
   - (No mention of label filtering at the repo level — GitHub doesn't
     expose that yet. So we use the workaround below.)

3. **Inbox filter** (Gmail / similar):
   - Filter for: `from:notifications@github.com Michaelpjob/ShoudiDive`
   - AND body contains: `severity:p0` OR `severity:p1`
   - Action: keep in inbox. Everything else: archive automatically.

4. **Heartbeat check**: visit issue tagged `system:alert-router-heartbeat`
   once a week. If the timestamp hasn't moved in > 30 min, the router
   itself is dead and your "no alerts" silence is suspicious — go look
   at the [alert-router runs](../../actions/workflows/alert-router.yml).

## What changes from the old setup

Before this routing existed:
- ~30 emails / day (Cloudflare flakes, beta deploys, GH 403 blips)
- Real bugs lost in the noise

After:
- ~1-3 emails / day on a normal week (only P0/P1)
- P2/P3 still visible in-app via the Issues tab if you want to look
- Rolling Issues mean one alert per root cause, not one per occurrence

## When to escalate a P2 → P1 manually

Sometimes a P2 advisory is actually urgent (e.g. the Vizcaíno data feed
broke on a release day). Just edit the rolling Issue's label from
`severity:p2` → `severity:p1`. Your subscription will start emailing
you on subsequent updates to that issue.

## When to demote a P0 → P3 (silence false positives)

If the router is misclassifying a transient as P0 (e.g. a one-off
NOAA NOMADS 503), close the Issue with a comment. The router will
re-evaluate on the next 15-min cycle; if the workflow has since gone
green it won't re-open.

---

_Architecture decision rationale lives in the original design discussion
(2026-05-26)._
