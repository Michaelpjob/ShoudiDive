# ShouldIDive — Baja Mexico expansion plan

Companion to `docs/expansion-regions.md` (which scopes PNW + tropical). Baja Mexico
extends the same region-aware infrastructure to cover the Pacific side of the Baja
California peninsula and the Sea of Cortez, presented as a single "Baja Mexico"
region in the switcher.

The scaffold (this PR) lands the `Region` dataclass entry, the frontend wiring,
and the CI workflow trio (`refresh-baja-data.yml`, `refresh-baja-wind.yml`,
`deploy-baja-beta.yml`). The follow-up PR series (PR-BAJA-2..5) fills in the
gaps that are flagged TBD below.

---

## 1. Geography — why two sub-bboxes

The Baja peninsula sits between two distinct water bodies:

- **Pacific (west):** California Current cold tongue in the north (Ensenada →
  Cedros, 14–18°C winter), warmer mid-latitude water near Magdalena Bay,
  warm tropical surface water by Cabo. Classic kelp-forest sites at Bahía
  Tortugas + the offshore islands (Cedros, San Benitos, Guadalupe).
- **Sea of Cortez (east):** warm clear gulf water, 20–31°C surface temps,
  strong tidal mixing in the Midriff (Tiburón / San Esteban / Ángel de la
  Guarda) bringing cold deep water to the surface mid-summer. The famous
  dive sites — Cabo Pulmo, La Paz / Espíritu Santo, Loreto Bay, Mulegé,
  Bahía de los Ángeles — all sit on this side.

The peninsula itself is ~100 km wide on average and 1,200 km long. A single
bbox would dedicate ~30% of every PNG to land pixels. The split mirrors
tropical's `gulf_se` + `caribbean` precedent:

```python
# in pipeline/regions/baja.py
subregion_bboxes = {
    "pacific":       dict(lat_min=22.5, lat_max=32.6, lng_min=-118.0, lng_max=-109.5),
    "sea_of_cortez": dict(lat_min=22.0, lat_max=32.0, lng_min=-115.5, lng_max=-106.5),
}
```

The two overlap over the peninsula's land mass; both passes get land-masked so
the overlap is harmless.

**Outer hull bbox** (what the frontend's map shows): lat 22.0–32.6°N,
lng −118.0 to −106.5°W.

---

## 2. Lat zones — the longitude-aware problem returns

Both `tropical` and `baja` have the same structural issue with the current
`classify_zone` walker: it's lat-only, but the Pacific side and the Sea of
Cortez side have very different water at the same latitude (Pacific at 25°N
is cool California-Current; Cortez at 25°N is warm gulf water).

For the scaffold we keep lat-only zones and accept the misclassification, in
exactly the same posture tropical/ does (see `tropical.py` module docstring):

```python
lat_zone_bounds = {
    "north_baja":  (28.00, 90.00),   # Ensenada → Cedros + Midriff Islands
    "mid_baja":    (24.50, 28.00),   # Vizcaíno + Magdalena + Loreto
    "south_baja":  (-90.00, 24.50),  # Cabo / La Paz / Cabo Pulmo
}
```

PR-TROP-1 (longitude-aware classifier, originally scoped for tropical) will
unblock proper zone calibration for baja too — the same machinery applies.

---

## 3. Viz model variant — `subtractive_tropical` forward-declaration

Picking the viz model for Baja was a non-obvious call:

| Variant                  | Fit                                              |
|--------------------------|--------------------------------------------------|
| `chl_based` (CA/PNW)     | Right for cold productive Pacific Baja north,  |
|                          | wrong for clear Sea of Cortez sites              |
| `subtractive_tropical`   | Right for clear Cortez sites + Cabo,            |
|                          | wrong for cold Pacific Baja north                |

We pick `subtractive_tropical` for the scaffold (matching the user-confirmed
choice on 2026-05-17). The formula itself doesn't exist yet — `tropical` also
declares the variant as a forward-declaration. PR-TROP-5 implements the math
in `viz_predict/`; baja inherits the fix automatically once that lands.

**Until PR-TROP-5:** visibility prediction falls back to chl-based CA model
output and predicts ~100 ft viz everywhere south of 28°N. Known wrong;
visibility chip should arguably be hidden on baja-beta until the variant
formula lands.

Alternative considered: a separate `baja_blend` variant that weights chl
(Pacific) and subtractive (Cortez) per-pixel based on longitude relative to
the peninsula. Deferred — until PR-TROP-5 sets the subtractive baseline, a
blend variant has nothing to blend against.

---

## 4. Per-layer data-source coverage

Coverage is mixed south of the US border. The scaffold ships fetchers
unchanged; this table captures what each one will actually return.

| Layer                    | Source            | Baja coverage                                 |
|--------------------------|-------------------|-----------------------------------------------|
| SST (daily, history)     | NASA OB.DAAC      | Global — fine                                 |
| SST 5-day forecast       | NOAA OISST/RTOFS  | Global — fine                                 |
| Chlorophyll              | NASA OB.DAAC      | Global — fine                                 |
| Kd490                    | NASA OB.DAAC      | Global — fine                                 |
| Wind (now + 5d)          | NOAA HRRR + GFS   | HRRR covers down to ~22°N — full Baja in HRRR |
| Swell (now + 5d)         | NOAA WaveWatch III| Global — fine                                 |
| Surface currents         | HFRNet            | **Zero** south of San Diego (~32.5°N)         |
| Surface currents (model) | NOAA RTOFS Global | Global — primary current source for baja      |
| Bathymetry               | GMRT              | Global — fine                                 |
| Coastline                | NOAA GSHHG        | Global — fine                                 |
| MPA polygons             | CDFW + NOAA NMS   | **None** — Mexican MPAs need CONANP fetcher   |
| Tide range               | NOAA CO-OPS       | **None** — stops at the US border             |
| Climatology              | NOAA              | Global — fine                                 |
| Precipitation (7d)       | NOAA CPC          | Global — fine                                 |

**Two structural gaps** that won't be fixed in the scaffold PR:

1. **MPA polygons.** Mexico has ~70 marine protected areas covering Baja
   waters: Islas del Pacífico Biosphere Reserve, Cabo Pulmo NP, Loreto Bay
   NP, Espíritu Santo NP, the Midriff (Ángel de la Guarda) protected area,
   etc. Source: CONANP's SIG portal at https://sig.conanp.gob.mx/.
   Format is not the WDPA polygon format `fetch_mpa.py` consumes. PR-BAJA-3
   adds a CONANP-specific ingest.

2. **Tide stations.** NOAA CO-OPS has one cooperator station near Ensenada
   (San Diego coverage technically extends south but station IDs are
   US-side). Mexican equivalents are CICESE (academic) and SEMAR (navy);
   neither publishes JSON in the format `fetch_tides.py` consumes. The
   `tide_stations=[]` in `baja.py` makes `fetch_tides.py` a no-op for the
   region and `fetch_visibility.py` falls back to its default tide_index.
   PR-BAJA-4 wires up a CICESE-compatible ingest.

---

## 5. Spot pins

No Baja entries in `_spot_lookup.json` yet. The classic sites to seed in
PR-BAJA-5:

**Pacific side:**
- Bahía Tortugas (Cedros), Isla Cedros, Isla San Benito, Isla Guadalupe
  (great-white-shark cage diving), Punta Banda / Todos Santos islands,
  Bahía Magdalena (gray whales — surface activity, not dive).

**Sea of Cortez:**
- Cabo Pulmo NP (the famous coral reef rebound), La Paz / Espíritu Santo
  / Los Islotes (sea-lion colony), Cerralvo, Las Ánimas, El Bajo seamount
  (hammerhead schools), Loreto / Isla Carmen / Isla Coronados (Baja's
  Coronados — distinct from CA's Coronados), Mulegé, Bahía de los Ángeles
  (whale sharks), Puerto Peñasco, San Felipe.

**Mainland-side Cortez:**
- Mazatlán, Topolobampo (mantas), San Carlos / Guaymas.

The seed pass should pull from BCS-published dive operator listings (Cabo
Pulmo Dive Center, Cortez Club La Paz, Dive Ninja Cabo, etc.) — same
methodology that seeded CA from BeachCitiesCuba + SouthCoastDivers.

---

## 6. SST range override

Sea-of-Cortez summer max hits ~31°C in the northern gulf (Puerto Peñasco /
San Felipe in August). Pacific-side winter min: ~14°C in the upwelling
tongue off Cedros in February. So:

```python
layer_range_overrides = {
    "sst":   (14.0, 32.0),
    "sst7d": (14.0, 32.0),
    "sst5d": (14.0, 32.0),
}
```

That's a slightly wider window than tropical (20–32) because the Pacific
Baja north end pulls the floor down to California-Current temps. Slightly
narrower than CA (9–25) on the cold end because Baja never sees winter
upwelling water below ~13°C.

---

## 7. Cloudflare Pages — one human-side prereq

The `deploy-baja-beta.yml` workflow assumes a `baja-beta` Cloudflare Pages
preview branch exists. Creating the branch is a one-time UI step in the
Cloudflare dashboard (Pages → shouldidive → Settings → Builds &
deployments → Preview branches → add `baja-beta`). Until that's done the
first deploy will fail with a "branch not configured" error.

---

## 8. PR plan

| PR        | Scope                                                                            |
|-----------|----------------------------------------------------------------------------------|
| BAJA-1 ✅ | Scaffold (this PR): `regions/baja.py`, registry, frontend wiring, CI workflows  |
| BAJA-2    | HYCOM Sea-of-Cortez nest (if a public endpoint exists; otherwise stays RTOFS)    |
| BAJA-3    | CONANP MPA polygon ingest                                                        |
| BAJA-4    | CICESE / SEMAR tide-station ingest                                               |
| BAJA-5    | Spot pin seed pass + popup metadata                                              |
| TROP-5    | `subtractive_tropical` viz formula (unblocks visibility for both baja + tropical) |
| TROP-1    | Longitude-aware `classify_zone` (unblocks proper zone calibration for both)      |

Once BAJA-2..5 land + TROP-5 + TROP-1 land, baja-beta can graduate from
"beta" to a chip drop in the production switcher.
