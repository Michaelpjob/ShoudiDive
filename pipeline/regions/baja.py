"""Baja Mexico region config — SKELETON.

Covers both sides of the Baja California peninsula: the Pacific
side (west, cold California-Current upwelling water in the north +
warmer mid-latitude water near Magdalena Bay) and the Sea of Cortez
(east, warm clear gulf water with strong tidal mixing in the Midriff
islands at the north end and 30°C+ surface temps in summer).

Two sub-bbox splits (``pacific`` + ``sea_of_cortez``) for the same
reason ``tropical`` splits into ``gulf_se`` + ``caribbean`` — one
overall bbox would push past runtime + data-volume caps for a single
matrix fetch job, and the Baja peninsula itself sits between the two
water bodies so a single bbox would waste ~30% of every PNG on land.
The two sub-bboxes overlap over the peninsula; the fetchers land-mask
both passes so the overlap is harmless.

## What's intentionally missing

* DriverCoefficients — the ``subtractive_tropical`` viz model is the
  right SHAPE for clear Sea-of-Cortez water (Cabo Pulmo, La Paz,
  Espíritu Santo) but the FORMULA itself doesn't exist yet — the
  ``tropical`` scaffold also declares ``subtractive_tropical`` as a
  forward-declaration. Visibility prediction for Baja will fall back
  to the chl-based CA model (and predict ~100 ft viz everywhere)
  until PR-TROP-5 lands the formula.
* HYCOM Sea-of-Cortez nest — the global HYCOM grid resolves the
  Cortez at 1/12°, fine for surface fields but too coarse for the
  Midriff-Islands choke points where tidal currents hit 4-5 kt.
  PR-BAJA-2 would tap CICESE's regional ROMS run if a public
  endpoint exists.
* CONANP MPA polygons — Mexico has ~70 marine protected areas south
  of the US border (Islas del Pacífico Biosphere Reserve, Cabo
  Pulmo NP, Loreto Bay NP, Espíritu Santo NP, …). The current
  ``fetch_mpa.py`` only pulls CDFW / NOAA NMS polygons; CONANP
  polygons live at https://sig.conanp.gob.mx/ and need a separate
  fetcher (PR-BAJA-3).
* Tide stations — NOAA CO-OPS coverage stops at the US border with
  one exception (Ensenada via international cooperator). The Mexican
  equivalent is CICESE / SEMAR; their station JSONs are not in the
  same format ``fetch_tides.py`` expects. Empty list here means
  ``fetch_tides.py`` is a no-op for baja and ``fetch_visibility.py``
  falls back to its default tide_index (PR-BAJA-4).
* Spot pins — no Baja entries in ``_spot_lookup.json`` yet. The
  classic sites (Cabo Pulmo, Sea of Cortez liveaboard route, Cedros,
  Guadalupe Island) come in PR-BAJA-5.

DO NOT consume this region from the running pipeline yet beyond the
fetcher passes that don't need any of the above — bathymetry, SST,
chl, kd490, wind, swell, currents will produce real data; visibility,
MPA overlay, tide_index, and the spot pin layer will be empty or wrong.
"""
from __future__ import annotations

from ._region import Region


# Outer hull of the two sub-region bboxes — the frontend's region
# switcher only knows the overall bounds; the per-subregion fetch
# jobs handle the actual data fetching.
#
# Northern bound 32.6°N: ~10 km of overlap with the CA region's
# southern edge at 31.8°N (CA bbox extends south to 31.8 so the
# Coronados show on the CA map; baja's north edge picks up just
# slightly north of that line so the regions visually butt against
# each other without a gap when a user switches).
#
# Southern bound 22.0°N: Cabo San Lucas sits at ~22.9°N. The extra
# 1° south gives the Gorda Banks (offshore seamount, classic
# spearfishing + striped marlin spot) breathing room on the south
# edge of the rendered map.
_HULL_BBOX = dict(lat_min=22.0, lat_max=32.6, lng_min=-118.0, lng_max=-106.5)


REGION = Region(
    name="baja",
    display_name="Baja Mexico",
    bbox=_HULL_BBOX,
    subregion_bboxes={
        # Pacific side: west of the peninsula. -118°W gives ~150 km
        # of open Pacific west of Punta Eugenia (-115°W) to capture
        # the California-Current upwelling tongue that drives the
        # cold-water kelp-forest sites at Cedros + San Benitos.
        # Eastern edge at -109.5°W intentionally crosses the
        # peninsula's east coast near Cabo so the southern tip
        # ("the corridor") is fully covered.
        "pacific":        dict(lat_min=22.5, lat_max=32.6,
                               lng_min=-118.0, lng_max=-109.5),
        # Sea of Cortez: between peninsula and mainland Mexico.
        # -115.5°W western edge crosses the peninsula east coast
        # near Bahía de los Ángeles; -106.5°W eastern edge sits
        # off Mazatlán. Lat 22.0-32.0 covers Cabo Corrientes-line
        # in the south up to the Colorado River delta in the
        # north (Puerto Peñasco / San Felipe).
        "sea_of_cortez": dict(lat_min=22.0, lat_max=32.0,
                               lng_min=-115.5, lng_max=-106.5),
    },
    lat_zone_bounds={
        # Lat-only zones — same caveat as tropical: ``classify_zone``
        # walks lat-only, so a point at 25°N on the Pacific side gets
        # the same zone as a point at 25°N in the Sea of Cortez,
        # despite very different water (cold-blue vs warm-green).
        # Longitude-aware classification lands with the same
        # PR-TROP-1 fix that tropical needs.
        #
        # north_pacific_upwelling: California-Current cold tongue.
        #   Pacific side from Ensenada (32.5°N) down through
        #   Cedros (28.3°N). On the Sea-of-Cortez side this lat
        #   band covers the Midriff Islands (Tiburón, San Esteban,
        #   Ángel de la Guarda) — water is COLD here too due to
        #   tidal mixing, so the same zone is approximately right
        #   for both sides.
        "north_baja":   (28.00, 90.00),
        # mid_baja: Vizcaíno transition + Magdalena Bay (Pacific)
        #   + Loreto / Mulegé (Cortez). Water mixes between cool
        #   California-Current and warm Tropical-Surface here.
        "mid_baja":     (24.50, 28.00),
        # south_baja: Cabo + La Paz + Cabo Pulmo. Warm clear water
        #   year-round, classic subtractive_tropical regime.
        "south_baja":   (-90.00, 24.50),
    },
    # Same dist labels as CA/PNW/tropical — see tropical.py for the
    # broader discussion on why renaming would coordinate-break the
    # DriverCoefficients dict. The labels still make sense for
    # Baja: nearshore (peninsular coast + mangrove shoals), islands
    # (Cedros / Guadalupe / Espíritu Santo / Tiburón etc.), offshore
    # (Gorda Banks + the seamounts west of Cabo).
    dist_labels=["nearshore", "islands", "offshore"],
    viz_model_variant="subtractive_tropical",
    data_dir_slug="baja",
    layer_range_overrides={
        # Sea-of-Cortez summer max: 31°C in the northern gulf in
        # August. Pacific-side winter min: ~14°C in the upwelling
        # tongue off Cedros in February. 14-32°C captures the
        # realistic seasonal swing across the whole region without
        # wasting encoding bits on temperatures we'll never see.
        "sst":   (14.0, 32.0),
        "sst7d": (14.0, 32.0),
        "sst5d": (14.0, 32.0),
    },
    # NOAA CO-OPS has effectively zero Mexican coverage. Ensenada
    # is the one cooperator station near the border. Empty list →
    # fetch_tides.py is a no-op for baja and fetch_visibility.py
    # falls back to its default tide_index. PR-BAJA-4 (CICESE /
    # SEMAR ingest) will populate this.
    tide_stations=[],
    notes=(
        "SKELETON — pacific + sea_of_cortez sub-bboxes, "
        "subtractive_tropical viz forward-declaration like tropical/. "
        "Empty tide_stations + no Mexican MPA polygons + no spot pins "
        "yet (PR-BAJA-3..5). Visibility prediction will be wrong "
        "until PR-TROP-5 implements the subtractive_tropical formula."
    ),
)
