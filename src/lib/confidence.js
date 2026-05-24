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

  // Generic per-layer freshness check (generated_at field).
  const genAt = info.generated_at || info.windows?.["1d"]?.generated_at;
  if (genAt) {
    const ageHours = (Date.now() - new Date(genAt).getTime()) / (1000 * 60 * 60);
    if (ageHours > 24) {
      delta -= 1;
      reasons.push(`layer last refreshed ${Math.round(ageHours)} h ago`);
    }
  }

  return { delta, reasons };
}

export function getLayerConfidence(layer) {
  const r = activeRegion();
  const base = STATIC_CONFIDENCE[r]?.[layer];
  if (!base) return null;
  const manifest = getDataState()?.manifest;
  const { delta, reasons } = dynamicModulation(layer, manifest);
  const score = Math.max(1, Math.min(5, base.score + delta));
  const label = CONFIDENCE_LABELS[score];
  return {
    score,
    ceilingScore: base.score,
    label: label.name,
    color: label.color,
    source: base.source,
    reason: base.reason,
    modReasons: reasons,
  };
}

// Region-level confidence = the WEAKEST layer score. Honest about the
// region's biggest gap rather than averaging it away. (Baja's current
// is 2/5 inferred — that's the headline; saying region=3.5 hides it.)
export function getRegionConfidence() {
  const r = activeRegion();
  const layers = STATIC_CONFIDENCE[r];
  if (!layers) return null;
  let weakest = 5;
  let weakestLayer = null;
  for (const [layerId, l] of Object.entries(layers)) {
    if (l.score < weakest) {
      weakest = l.score;
      weakestLayer = layerId;
    }
  }
  const label = CONFIDENCE_LABELS[weakest];
  return {
    score: weakest,
    label: label.name,
    color: label.color,
    weakestLayer,
  };
}
