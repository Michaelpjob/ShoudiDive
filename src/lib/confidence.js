// Per-region, per-layer confidence scoring.
//
// Two layers of evidence:
//   1. STATIC ceiling — per (region, layer) score reflecting the
//      data source(s) available there: HFRNet only covers US west coast,
//      gfswave atlocn has known island-shelf issues, etc. This is the
//      "best you can hope for in this region" upper bound.
//   2. DYNAMIC modulation — today's actual coverage + freshness from the
//      live manifest. If chl_1d coverage_frac < 0.4 (mostly cloud-covered)
//      or mean_age_days > 5, drop the score by 1. If a layer's
//      generated_at is > 24 h ago, drop by 1.
//
// The final score per (region, layer, today) drives the colored dot
// next to each layer chip + the region badge in the top bar.
//
// Score-to-label mapping:
//   5 = Validated   (calibrated against ground truth)
//   4 = Observed    (direct measurement, multiple sources)
//   3 = Modeled     (model output, no observations at the cell)
//   2 = Inferred    (proxy derived from other inputs)
//   1 = Climatology (long-term average, no recent signal)

import { activeRegion } from "./region.js";
import { getDataState } from "./dataSource.js";

const STATIC_CONFIDENCE = {
  ca: {
    sst:     { score: 5, source: "MUR satellite",        reason: "Validated against pier sensors + ground-truth" },
    chl:     { score: 4, source: "MODIS/VIIRS blend",    reason: "Multi-source NRT, NASA OB.DAAC" },
    wind:    { score: 5, source: "HRRR 3 km hourly",     reason: "NOAA operational forecast" },
    swell:   { score: 4, source: "WW3 gfswave wcoast",   reason: "NOAA model, ~18 km grid" },
    current: { score: 4, source: "HFRNet 6 km + tide/wind", reason: "Observed nearshore via HF radar; inferred offshore" },
    viz:     { score: 4, source: "viz_predict model",    reason: "Calibrated against CA dive ground-truth ingestion" },
  },
  baja: {
    sst:     { score: 5, source: "MUR satellite",        reason: "Same satellite + algorithm as CA" },
    chl:     { score: 4, source: "MODIS/VIIRS blend",    reason: "Multi-source NRT, NASA OB.DAAC" },
    wind:    { score: 4, source: "HRRR + GFS",           reason: "HRRR (3 km) north of ~21°N; GFS (~12 km) south of CONUS" },
    swell:   { score: 3, source: "WW3 wcoast + SMB chop", reason: "WW3 covers Pacific; Sea of Cortez is wind-chop fetch-limited inference" },
    current: { score: 2, source: "Tide + Ekman wind",    reason: "No HFRNet south of US border — pure inference" },
    viz:     { score: 3, source: "viz_predict model",    reason: "Coefficients ported from CA; not yet validated against Baja ground-truth" },
  },
  pnw: {
    sst:     { score: 5, source: "MUR satellite",        reason: "Same satellite + algorithm as CA" },
    chl:     { score: 3, source: "MODIS/VIIRS",          reason: "Marine layer + fog frequently block satellite passes" },
    wind:    { score: 5, source: "HRRR 3 km hourly",     reason: "Full HRRR coverage" },
    swell:   { score: 4, source: "WW3 gfswave wcoast",   reason: "NOAA model, ~18 km grid" },
    current: { score: 4, source: "HFRNet + tide/wind",   reason: "Outer coast observed via HFRNet; Salish Sea is tide-inferred" },
    viz:     { score: 2, source: "viz_predict model",    reason: "Not calibrated to PNW ground-truth — beta" },
  },
  tropical: {
    sst:     { score: 5, source: "MUR satellite",        reason: "Same satellite + algorithm as CA" },
    chl:     { score: 4, source: "MODIS/VIIRS",          reason: "Lower cloud cover than CA on average" },
    wind:    { score: 4, source: "HRRR Gulf + GFS",      reason: "HRRR covers Gulf coast; GFS for Caribbean and east FL" },
    swell:   { score: 3, source: "WW3 atlocn",           reason: "Atlantic basin model; known issues at small-island shelves" },
    current: { score: 2, source: "Tide + Ekman wind",    reason: "No HFRNet outside US west coast — pure inference" },
    viz:     { score: 2, source: "viz_predict model",    reason: "Not calibrated to Caribbean ground-truth — beta" },
  },
};

const CONFIDENCE_LABELS = {
  5: { name: "Validated",    color: "rgb(34, 197, 94)" },   // green-500
  4: { name: "Observed",     color: "rgb(132, 204, 22)" },  // lime-500
  3: { name: "Modeled",      color: "rgb(234, 179, 8)" },   // yellow-500
  2: { name: "Inferred",     color: "rgb(249, 115, 22)" },  // orange-500
  1: { name: "Climatology",  color: "rgb(220, 38, 38)" },   // red-600
};

// Per-layer freshness budget (days) — the OBSERVATION age beyond which
// we've "lost confidence" in the live reading. Mirrors the pipeline's
// LAYER_DATE_MAX_DAYS (pipeline/check_manifest_freshness.py) so the
// user-facing signal and the CI freshness guard agree on what "stale"
// means. Within budget the layer reads at its normal ceiling; past it the
// score is capped so a frozen layer can't keep showing "Observed".
const LAYER_FRESH_DAYS = {
  sst: 4, chl: 7, kd490: 14, wind: 1, swell: 2, current: 2, viz: 2,
};

// Age (days) of a layer's most recent OBSERVATION, from the manifest's
// window dates — NOT its refresh time, which is what `generated_at`
// tracks. (During the 2026-06 NOAA outage the refresh kept "running" but
// the observation dates froze; observation age is the honest signal.)
// Falls back to generated_at only when a layer carries no date list.
export function layerDataAgeDays(layer, manifest) {
  const info = manifest?.layers?.[layer];
  if (!info) return null;
  const dates = info.windows?.["1d"]?.dates || info.windows?.["2d"]?.dates;
  if (Array.isArray(dates) && dates.length) {
    const t = Date.parse(`${dates[dates.length - 1]}T00:00:00Z`);
    if (!Number.isNaN(t)) return (Date.now() - t) / 86400000;
  }
  const genAt = info.generated_at || info.windows?.["1d"]?.generated_at;
  if (genAt) {
    const t = Date.parse(genAt);
    if (!Number.isNaN(t)) return (Date.now() - t) / 86400000;
  }
  return null;
}

// Translate observation age (+ an explicit pipeline "held/estimated" flag,
// set when last-good is served because no live source returned) into a
// confidence CEILING and a short user-facing tag. Quiet inside the
// freshness budget; caps the score hard once we're past it.
function staleness(layer, manifest) {
  const info = manifest?.layers?.[layer];
  const age = layerDataAgeDays(layer, manifest);
  const budget = LAYER_FRESH_DAYS[layer];
  if (info && (info.held === true || info.estimated === true)) {
    return { cap: 1, ageDays: age, level: "estimated", tag: "estimated",
             note: "estimated — live source unavailable, showing last good data" };
  }
  if (age == null || budget == null) return { cap: 5, ageDays: age, level: "ok", tag: null, note: null };
  const d = Math.round(age);
  if (age > budget * 2) return { cap: 1, ageDays: age, level: "stale", tag: `${d}d old`,
                                 note: `${d} days old — well past the ${budget}-day freshness budget` };
  if (age > budget)     return { cap: 2, ageDays: age, level: "stale", tag: `${d}d old`,
                                 note: `${d} days old — past the ${budget}-day freshness budget` };
  if (age > budget * 0.6) return { cap: 4, ageDays: age, level: "aging", tag: null, note: `${d} days old` };
  return { cap: 5, ageDays: age, level: "ok", tag: null, note: null };
}

// Forecast-horizon decay. Skill drops with lead time — HRRR is observed
// for ~18 h then becomes the GFS forecast, gfswave is reasonable to
// ~3 days, persistence_decay SST loses faith beyond +3.
//
// Returns a score delta (negative number) given the layer and the
// number of days INTO the forecast (0 = today, positive = future,
// negative = history). Historical scrubs return 0 (observed always).
function horizonDecay(layer, horizonDays) {
  if (horizonDays == null || horizonDays <= 0) return { delta: 0, reason: null };

  if (layer === "sst") {
    // SST forecast = persistence_decay, doesn't track real ocean physics
    // beyond a few days. Penalize sooner than the dynamical layers.
    if (horizonDays >= 4) return { delta: -2, reason: `forecast +${horizonDays} d (persistence skill fades)` };
    if (horizonDays >= 2) return { delta: -1, reason: `forecast +${horizonDays} d (persistence drift starting)` };
    return { delta: 0, reason: null };
  }

  if (layer === "wind" || layer === "swell" || layer === "current") {
    // Dynamical models (HRRR / WW3 / GFS) hold reasonable skill out to
    // ~3 days. Drop by 1 at day 3 and again at day 5 (forecast-end).
    if (horizonDays >= 5) return { delta: -2, reason: `forecast +${horizonDays} d (NOAA model skill drop)` };
    if (horizonDays >= 3) return { delta: -1, reason: `forecast +${horizonDays} d (NOAA model uncertainty growing)` };
    return { delta: 0, reason: null };
  }

  // chl / viz have no time-slider on their own layer (chl is one snapshot,
  // viz is one prediction). Horizon is irrelevant — return 0.
  return { delta: 0, reason: null };
}

// Pull dynamic signals from today's manifest. Returns score-adjustments
// and human-readable reasons so the tooltip can explain WHY today's
// number is lower than the ceiling.
function dynamicModulation(layer, manifest) {
  let delta = 0;
  const reasons = [];
  if (!manifest) return { delta, reasons };
  const info = manifest.layers?.[layer];
  if (!info) return { delta, reasons };

  // chl: coverage + age from the windows.1d entry.
  if (layer === "chl") {
    const win = info.windows?.["1d"];
    const cov = win?.coverage_frac;
    const age = win?.mean_age_days;
    if (cov != null && cov < 0.4) {
      delta -= 1;
      reasons.push(`only ${Math.round(cov * 100)}% chl coverage today (cloud)`);
    }
    if (age != null && age > 5) {
      delta -= 1;
      reasons.push(`chl observation is ${age.toFixed(1)} days old`);
    }
  }

  // Fallback source: the pipeline sets source_fallback when the primary was
  // unavailable and a coarser/gappier backup stood in (SST→OISST 0.25°,
  // chl→raw VIIRS). It's live, so not "stale" — but lower confidence, and the
  // user should know which source they're actually looking at.
  if (info.source_fallback) {
    delta -= 1;
    reasons.push(`via ${info.source || "backup source"} (primary unavailable)`);
  }

  // Layer staleness (observation age vs per-layer budget) is handled by
  // staleness() in getLayerConfidence — it caps the score and supplies the
  // user-facing tag, replacing the old flat "refreshed >24 h ago → −1".
  return { delta, reasons };
}

export function getLayerConfidence(layer, opts = {}) {
  const r = activeRegion();
  const base = STATIC_CONFIDENCE[r]?.[layer];
  if (!base) return null;
  const manifest = getDataState()?.manifest;
  const { delta: dynDelta, reasons: dynReasons } = dynamicModulation(layer, manifest);
  const { delta: horDelta, reason: horReason } = horizonDecay(layer, opts.horizonDays);
  const st = staleness(layer, manifest);
  const reasons = [...dynReasons];
  if (st.note) reasons.push(st.note);
  if (horReason) reasons.push(horReason);
  let score = Math.max(1, Math.min(5, base.score + dynDelta + horDelta));
  // Staleness CAPS the ceiling: a layer past its freshness budget can't
  // keep reading "Observed" no matter how good its source normally is.
  score = Math.min(score, st.cap);
  const label = CONFIDENCE_LABELS[score];
  return {
    score,
    ceilingScore: base.score,
    label: label.name,
    color: label.color,
    source: base.source,
    reason: base.reason,
    modReasons: reasons,
    ageDays: st.ageDays,
    stale: st.level === "stale" || st.level === "estimated",
    staleLevel: st.level,        // "ok" | "aging" | "stale" | "estimated"
    staleTag: st.tag,            // short inline tag, e.g. "5d old" / "estimated"
  };
}

// Region-level confidence = the WEAKEST layer score. Honest about the
// region's biggest gap rather than averaging it away. (Baja's current
// is 2/5 inferred — that's the headline; saying region=3.5 hides it.)
export function getRegionConfidence() {
  const r = activeRegion();
  const layers = STATIC_CONFIDENCE[r];
  if (!layers) return null;
  // Weakest LIVE layer score (staleness included), not the static ceiling —
  // so the top-bar badge drops when a layer has actually gone stale, which
  // is the whole point of surfacing lost confidence.
  let weakest = 5;
  let weakestLayer = null;
  let stale = false;
  for (const layerId of Object.keys(layers)) {
    const c = getLayerConfidence(layerId);
    if (!c) continue;
    if (c.score < weakest) {
      weakest = c.score;
      weakestLayer = layerId;
      stale = c.stale;
    }
  }
  const label = CONFIDENCE_LABELS[weakest];
  return {
    score: weakest,
    label: label.name,
    color: label.color,
    weakestLayer,
    stale,
  };
}
