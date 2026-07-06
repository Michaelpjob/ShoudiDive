# paddies-model — Kelp Paddy Finder drift model (productionized)

Generates `public/paddies/data.json` for the **Kelp Paddy Finder**
(shouldidive.com/paddies/). It drifts recently-shed kelp cohorts forward on
**HFRNet 6 km observed currents + Open-Meteo** fill, promotes the live crowd
catch-reports, and renders the statistical region where floating paddies are
concentrated across a **−3..+2 day** window (historical → today → forecast).

## It self-updates daily
`.github/workflows/refresh-paddies.yml` (daily cron) runs:

```
python paddies-model/build_site.py public/paddies
```

…then commits the fresh `data.json` to `main` and fires the prod deploy. So the
tool stays live like the rest of the site instead of serving a hand-built
snapshot. Refresh manually anytime via that workflow's **Run workflow**
(`workflow_dispatch`), or locally:

```
pip install -r paddies-model/requirements.txt
python paddies-model/build_site.py <ShoudiDive>/public/paddies
```

## Inputs (all public)
- **HFRNet 6 km currents** — NDBC THREDDS (`dods.ndbc.noaa.gov`) via `pydap`.
- **Open-Meteo** — marine (waves) + forecast (weather).
- **Live crowd reports** — `shouldidive.com/api/paddies/reports` (degrades to
  physics-only if unreachable).
- **Dive-spot bundles** — local `public/data/spots/*/bundle.json`.

## Origin
Productionized from the standalone research repo
[`Michaelpjob/kelp-drift-proto`](https://github.com/Michaelpjob/kelp-drift-proto)
— deeper model documentation, calibration notes, and the experiment scripts live
there. Keep that repo's `*.py` and this copy in sync when the model changes.
