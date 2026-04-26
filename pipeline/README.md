# Pipeline

Daily fetch script that pulls real ocean data and writes it to `../public/data/`
where the Vite dev server picks it up.

## Setup

```
python -m venv .venv
.venv\Scripts\activate           # Windows
# or: source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

## Run

```
python fetch.py
```

Default: pulls the latest 3 valid days for each layer from NOAA CoastWatch
ERDDAP, builds 1/2/3-day composites, encodes each as a PNG, and writes a
manifest. Output lands in `../public/data/`. The dev server reads that
directory at `/data/...`. No credentials required.

Layers fetched:

- **sst** — GHRSST MUR L4, 1 km, gap-filled (`jplMURSST41`)
- **chl** — VIIRS S-NPP + NOAA-20 NRT, gap-filled (`nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily`)

Each layer walks back from the end date until it finds 3 valid days, so
the two products' different publication lags don't matter — you get the
latest 3 of each, independently.

Useful flags:

- `--end-date 2026-04-23` — pin the last day to look from (helpful for
  backfilling).
- `--layer sst` (or `chl`) — fetch a single layer instead of all.
- Per-layer stride is set in `fetch.py`'s `LAYERS` table (SST defaults to
  stride 2 for ~2 km cells, chl to stride 1 since the source is already
  9 km).

## What the manifest looks like

```json
{
  "generated_at": "2026-04-25T20:14:00Z",
  "bbox": [-124.0, 32.4, -117.0, 37.6],
  "layers": {
    "sst": {
      "range": [9, 25],
      "scale": "linear",
      "unit": "degC",
      "grid": { "width": 175, "height": 130 },
      "windows": {
        "1d": { "url": "/data/sst_1d.png", "dates": ["2026-04-24"] },
        "2d": { "url": "/data/sst_2d.png", "dates": ["2026-04-23", "2026-04-24"] },
        "3d": { "url": "/data/sst_3d.png", "dates": ["2026-04-22", "2026-04-23", "2026-04-24"] }
      }
    }
  }
}
```

PNG encoding: 8-bit grayscale. Pixel `0` = no-data, `1..255` = linear over
the layer's range. The frontend decodes via canvas.

## Why NOAA CoastWatch instead of Copernicus

The original build brief points at Copernicus Marine for chlorophyll, which
needs a free account and the `copernicusmarine` Python client. NOAA
CoastWatch ERDDAP serves a comparable gap-filled VIIRS chl-a product with
no auth and from the same endpoint we already use for SST, so the pipeline
is single-source and dependency-light. The downside: NOAA's product is
~9 km native resolution vs Copernicus' 4 km. If you ever need finer chl,
swap the dataset ID in `LAYERS["chl"]` and add credentials handling.

## Caching

Raw ERDDAP NetCDF files cache to `.cache/`. Delete that directory to force
a refetch.
