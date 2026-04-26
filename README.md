# CA Coast Conditions

Daily satellite-derived sea surface temperature and water clarity for the
Southern + Central California coast (32.4°–37.6°N, -124° to -117°). Live at
[shouldidive.com](https://shouldidive.com).

## What's here

- `src/` — Vite + React frontend
- `pipeline/` — Python script that pulls fresh SST and chlorophyll-a from
  NOAA CoastWatch ERDDAP and writes manifest + PNGs into `public/data/`
- `.github/workflows/refresh-data.yml` — daily cron that runs the pipeline
  and commits the result; Cloudflare Pages redeploys on push

Data sources, both no-auth, fetched daily:

- **SST** — GHRSST MUR L4, 1 km, gap-filled (`jplMURSST41`)
- **Chlorophyll** — VIIRS S-NPP + NOAA-20 NRT, 9 km, gap-filled
  (`nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily`)

## Local dev

```
npm install
npm run dev          # frontend on http://127.0.0.1:5173

# one-time pipeline setup
cd pipeline
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# pull fresh data
.venv\Scripts\python.exe fetch.py
```

`fetch.py` writes `public/data/manifest.json` plus `{sst,chl}_{1d,2d,3d}.png`.
Vite serves them at `/data/...` and `src/lib/dataSource.js` decodes them at
boot.

## Deployment (one-time setup)

### 1. Push to GitHub

Create an empty repo (no README, no .gitignore — we have those). Then:

```
git remote add origin git@github.com:<user>/ca-coast-conditions.git
git branch -M main
git push -u origin main
```

### 2. Connect Cloudflare Pages

1. [Cloudflare dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**
2. Pick the GitHub repo. Authorize if prompted.
3. Build configuration:
   - Framework preset: **Vite**
   - Build command: `npm run build`
   - Build output directory: `dist`
   - Root directory: leave empty
4. Save and deploy. First build takes ~1 minute.

You'll get a `*.pages.dev` URL immediately. Verify it works.

### 3. Custom domain (shouldidive.com)

In the Pages project → **Custom domains** → **Set up a custom domain** →
enter `shouldidive.com`.

- If shouldidive.com's DNS is on Cloudflare: one-click attach.
- If it's elsewhere: Cloudflare gives you a CNAME target — add a CNAME
  record at the registrar pointing `shouldidive.com` (and `www`) to the
  Cloudflare-provided target. Or move DNS to Cloudflare for free TLS at
  the edge.

After DNS propagates (minutes to a few hours) the site is live at
shouldidive.com with auto-renewing TLS.

### 4. Confirm the daily cron works

Go to the GitHub repo → **Actions** → **Refresh ocean data** → **Run
workflow**. It'll fetch fresh data and commit. Cloudflare Pages picks up
the push and redeploys within ~30 seconds. After that the cron fires
automatically every 24 h at 06:00 UTC.

## Costs

Free tier of all of Cloudflare Pages, GitHub Actions, and the underlying
NOAA endpoints. ~85 KB of new PNG data per day in git history.
