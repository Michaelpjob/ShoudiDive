// Map projection helpers and mocked sea data.
//
// 2026-05-11 — BBOX is now region-aware (PR-FE-1). The frontend reads
// `activeRegion()` at module load and selects the matching bbox so the
// projection math (project/unproject/getFitted/GEO_ASPECT) all align
// with whichever region's data the manifest carries. Switching
// regions in the RegionSwitcher triggers a full reload (see
// region.js → setActiveRegion), so it's safe for BBOX to be a const
// at module level — it just gets a different value on the next boot.
//
// CA history that matters:
//   2026-05-09 — bumped CA latMax 37.6 → 42.0 to cover the full coast
//                (NorCal expansion, docs/expansion-norcal.md).
//   2026-05-10 — bumped CA lngMin -124.0 → -124.6 for Cape Mendocino,
//                restored lngMax to -116.8 for La Jolla / Coronados.
//   2026-05-13 — bumped CA lngMin -124.6 → -127.0 to widen the view
//                west of the coast (Cape Mendocino → ~270 km of
//                Pacific buffer). GEO_ASPECT ~0.80 (taller-than-wide).
//   2026-05-14 — bumped CA lngMin -127.0 → -128.5 (NorCal pre-launch).
//                Cape Mendocino was crowded against the west edge in
//                the locked aspect ratio; +1.5° gives ~130 km more
//                offshore margin. GEO_ASPECT now ~0.92 (still slight
//                tall-bias so mobile portrait UX unchanged).
//
// The values here must stay in lockstep with pipeline/regions/*.py.
// If you bump a region's bbox there, mirror it here. (A drift test
// across these two sides is a follow-up TODO.)
import { activeRegion } from "./region.js";

const REGION_BBOX = {
  ca:       { latMin: 31.8, latMax: 42.0, lngMin: -128.5, lngMax: -116.8 },
  pnw:      { latMin: 42.0, latMax: 49.0, lngMin: -127.0, lngMax: -122.0 },
  tropical: { latMin: 10.0, latMax: 31.0, lngMin:  -98.0, lngMax:  -60.0 },
  baja:     { latMin: 22.0, latMax: 32.6, lngMin: -118.0, lngMax: -106.5 },
};
export const BBOX = REGION_BBOX[activeRegion()] || REGION_BBOX.ca;

// Geographic aspect ratio (lng-degrees-as-distance / lat-degrees) at the
// bbox's mid latitude. Lng degrees shrink by cos(lat); we compute it once
// against the centre so 1 unit of x and y on the map represent the same
// distance on the ground. ~1.03 for our CA bbox — almost square.
const _MID_LAT_RAD = ((BBOX.latMin + BBOX.latMax) / 2) * Math.PI / 180;
const _COS_MID_LAT = Math.cos(_MID_LAT_RAD);
export const GEO_ASPECT =
  ((BBOX.lngMax - BBOX.lngMin) * _COS_MID_LAT) /
  (BBOX.latMax - BBOX.latMin);

// Pillarbox / letterbox the bbox inside an arbitrary container so x and y
// pixels both represent the same on-the-ground distance regardless of the
// container's aspect ratio. Returns the rectangle inside (0..w, 0..h) that
// the geographic content should occupy.
//
// Aspect ratio is ALWAYS preserved — `1 km north` and `1 km east` are
// always the same number of pixels. A previous (2026-05-09 morning)
// attempt to fall back to aspect-fill when geo-aspect ≠ container-aspect
// produced visibly stretched land features for the NorCal expansion
// (CA coast looked ~3x too wide). Pillarbox is the right tradeoff —
// the desktop layout's aspect should be matched to GEO_ASPECT instead
// (handled at the .map-stage CSS level, not here).
export function getFitted(w, h) {
  if (!(w > 0) || !(h > 0)) {
    return { marginX: 0, marginY: 0, innerW: w || 0, innerH: h || 0 };
  }
  const containerAspect = w / h;
  let innerW;
  let innerH;
  if (containerAspect > GEO_ASPECT) {
    // Wider than geography → pillarbox: height fills, sides have margin.
    innerH = h;
    innerW = h * GEO_ASPECT;
  } else {
    // Taller than geography → letterbox: width fills, top/bottom have margin.
    innerW = w;
    innerH = w / GEO_ASPECT;
  }
  return {
    marginX: (w - innerW) / 2,
    marginY: (h - innerH) / 2,
    innerW,
    innerH,
  };
}

export function project(lng, lat, w, h) {
  const { marginX, marginY, innerW, innerH } = getFitted(w, h);
  const x = marginX + ((lng - BBOX.lngMin) / (BBOX.lngMax - BBOX.lngMin)) * innerW;
  const y = marginY + ((BBOX.latMax - lat) / (BBOX.latMax - BBOX.latMin)) * innerH;
  return [x, y];
}

export function unproject(x, y, w, h) {
  const { marginX, marginY, innerW, innerH } = getFitted(w, h);
  const lng = BBOX.lngMin + ((x - marginX) / innerW) * (BBOX.lngMax - BBOX.lngMin);
  const lat = BBOX.latMax - ((y - marginY) / innerH) * (BBOX.latMax - BBOX.latMin);
  return [lng, lat];
}

// Stylized California coastline (approximate, hand-tuned to bounding box).
// Coordinates are [lng, lat], polyline runs roughly N→S.
export const COASTLINE = [
  [-122.05, 37.50],
  [-121.95, 37.20],
  [-121.93, 36.97],
  [-121.88, 36.80],
  [-121.78, 36.62],
  [-121.90, 36.55],
  [-121.94, 36.50],
  [-121.93, 36.30],
  [-121.88, 36.10],
  [-121.75, 35.90],
  [-121.55, 35.75],
  [-121.45, 35.65],
  [-121.30, 35.55],
  [-121.10, 35.40],
  [-120.95, 35.35],
  [-120.85, 35.25],
  [-120.85, 35.15],
  [-120.75, 35.05],
  [-120.65, 34.95],
  [-120.65, 34.85],
  [-120.60, 34.75],
  [-120.55, 34.60],
  [-120.50, 34.50],
  [-120.45, 34.45],
  [-120.30, 34.45],
  [-120.10, 34.43],
  [-119.85, 34.42],
  [-119.70, 34.42],
  [-119.50, 34.40],
  [-119.30, 34.30],
  [-119.25, 34.20],
  [-119.05, 34.18],
  [-118.85, 34.10],
  [-118.65, 34.05],
  [-118.55, 34.00],
  [-118.50, 33.92],
  [-118.45, 33.82],
  [-118.38, 33.72],
  [-118.30, 33.72],
  [-118.25, 33.75],
  [-118.10, 33.72],
  [-118.00, 33.66],
  [-117.95, 33.62],
  [-117.85, 33.55],
  [-117.75, 33.50],
  [-117.60, 33.40],
  [-117.55, 33.35],
  [-117.45, 33.20],
  [-117.40, 33.10],
  [-117.35, 33.00],
  [-117.30, 32.92],
  [-117.28, 32.84],
  [-117.22, 32.74],
  [-117.15, 32.65],
  [-117.12, 32.55],
  [-117.13, 32.42],   // US/MX border
  [-117.10, 32.30],   // Tijuana coast
  [-117.05, 32.20],
  [-117.00, 32.05],
  [-116.92, 31.95],
  [-116.88, 31.85],
  [-116.85, 31.80],   // south edge of bbox
];

// Channel Islands + Coronados (simplified centers + radii in degrees).
export const ISLANDS = [
  { name: "San Miguel",       lng: -120.37, lat: 34.04, rx: 0.10, ry: 0.04 },
  { name: "Santa Rosa",       lng: -120.10, lat: 33.97, rx: 0.13, ry: 0.06 },
  { name: "Santa Cruz",       lng: -119.75, lat: 33.99, rx: 0.18, ry: 0.06 },
  { name: "Anacapa",          lng: -119.40, lat: 34.00, rx: 0.05, ry: 0.02 },
  { name: "San Nicolas",      lng: -119.50, lat: 33.25, rx: 0.08, ry: 0.04 },
  { name: "Santa Barbara I.", lng: -119.03, lat: 33.48, rx: 0.03, ry: 0.02 },
  { name: "Santa Catalina",   lng: -118.45, lat: 33.39, rx: 0.13, ry: 0.05 },
  { name: "San Clemente",     lng: -118.50, lat: 32.90, rx: 0.10, ry: 0.04 },
  // Las Islas Coronado — popular SoCal dive destination off Tijuana
  { name: "N. Coronado",      lng: -117.30, lat: 32.42, rx: 0.018, ry: 0.030 },
  { name: "M. Coronado",      lng: -117.27, lat: 32.40, rx: 0.008, ry: 0.010 },
  { name: "S. Coronado",      lng: -117.25, lat: 32.38, rx: 0.022, ry: 0.035 },
];

// Region-aware saved spots. CA's list is hand-curated; PNW + tropical
// get a starter set chosen from the spot pins enumerated in
// docs/expansion-regions.md (PNW § 2 spot list, tropical § 3 spot
// list). The full curated rosters live behind PR-PNW-1 and PR-TROP-7;
// these placeholders exist so the sidebar / mobile sheet has
// SOMETHING geographically meaningful for non-CA regions instead of
// leaking CA spots into a Pacific NW or Caribbean map.
const REGION_SAVED_SPOTS = {
  ca: [
    { id: "monterey",  name: "Monterey",       lng: -121.92, lat: 36.62 },
    { id: "morro",     name: "Morro Bay",      lng: -120.88, lat: 35.36 },
    { id: "pt-concep", name: "Pt. Conception", lng: -120.47, lat: 34.45 },
    { id: "santabarb", name: "Santa Barbara",  lng: -119.70, lat: 34.40 },
    { id: "santacruz", name: "Santa Cruz I.",  lng: -119.75, lat: 34.05 },
    { id: "malibu",    name: "Malibu",         lng: -118.78, lat: 34.02 },
    { id: "catalina",  name: "Catalina",       lng: -118.45, lat: 33.39 },
    { id: "lajolla",   name: "La Jolla",       lng: -117.28, lat: 32.85 },
    { id: "sandiego",  name: "San Diego",      lng: -117.18, lat: 32.70 },
    { id: "coronados", name: "Coronados",      lng: -117.27, lat: 32.40 },
  ],
  pnw: [
    { id: "edmonds",     name: "Edmonds UWP",   lng: -122.382, lat: 47.812 },
    { id: "seacrest",    name: "Seacrest",      lng: -122.378, lat: 47.578 },
    { id: "three-tree",  name: "Three Tree Pt", lng: -122.391, lat: 47.451 },
    { id: "limekiln",    name: "Lime Kiln",     lng: -123.151, lat: 48.516 },
    { id: "pile-pt",     name: "Pile Point",    lng: -123.075, lat: 48.471 },
    { id: "salt-creek",  name: "Salt Creek",    lng: -123.703, lat: 48.166 },
    { id: "neah-bay",    name: "Neah Bay",      lng: -124.620, lat: 48.367 },
    { id: "sund-rock",   name: "Sund Rock",     lng: -123.158, lat: 47.435 },
    { id: "octopus-hole",name: "Octopus Hole",  lng: -123.149, lat: 47.421 },
    { id: "yaquina-head",name: "Yaquina Head",  lng: -124.078, lat: 44.674 },
    { id: "sunset-bay",  name: "Sunset Bay",    lng: -124.378, lat: 43.337 },
  ],
  tropical: [
    { id: "blue-heron",  name: "Blue Heron Br",   lng:  -80.045, lat: 26.783 },
    { id: "jupiter",     name: "Jupiter Ledge",   lng:  -80.057, lat: 26.943 },
    { id: "looe-key",    name: "Looe Key",        lng:  -81.408, lat: 24.547 },
    { id: "molasses",    name: "Molasses Reef",   lng:  -80.402, lat: 25.011 },
    { id: "vandenberg",  name: "Vandenberg",      lng:  -81.788, lat: 24.488 },
    { id: "spiegel",     name: "Spiegel Grove",   lng:  -80.298, lat: 25.069 },
    { id: "flower-gdn",  name: "Flower Garden",   lng:  -93.846, lat: 27.872 },
    { id: "stuart-cove", name: "Stuart Cove",     lng:  -77.531, lat: 24.991 },
    { id: "bloody-bay",  name: "Bloody Bay Wall", lng:  -80.097, lat: 19.711 },
    { id: "palancar",    name: "Palancar",        lng:  -87.027, lat: 20.358 },
    { id: "bonaire-1k",  name: "1000 Steps",      lng:  -68.397, lat: 12.219 },
    { id: "blue-hole",   name: "Blue Hole",       lng:  -87.535, lat: 17.316 },
  ],
  baja: [
    // Pacific side
    { id: "san-benito",   name: "Isla San Benito", lng: -115.55, lat: 28.31 },
    { id: "cedros",       name: "Isla Cedros",     lng: -115.20, lat: 28.20 },
    { id: "bahia-tort",   name: "Bahía Tortugas",  lng: -114.88, lat: 27.69 },
    { id: "sacramento",   name: "Sacramento Reef", lng: -116.04, lat: 30.55 },
    // Cabo corridor
    { id: "cabo-falso",   name: "Cabo Falso",      lng: -110.00, lat: 22.87 },
    { id: "cabo",         name: "Cabo San Lucas",  lng: -109.92, lat: 22.89 },
    { id: "gordo-banks",  name: "Gordo Banks",     lng: -109.34, lat: 22.95 },
    // Sea of Cortez — south
    { id: "cabo-pulmo",   name: "Cabo Pulmo",      lng: -109.43, lat: 23.43 },
    { id: "los-islotes",  name: "Los Islotes",     lng: -110.39, lat: 24.59 },
    { id: "espiritu-stm", name: "Espíritu Santo",  lng: -110.37, lat: 24.50 },
    { id: "el-bajo",      name: "El Bajo",         lng: -109.96, lat: 24.59 },
    { id: "marisla",      name: "Marisla Smt",     lng: -110.40, lat: 24.45 },
    { id: "cerralvo",     name: "Cerralvo",        lng: -109.86, lat: 24.27 },
    // Sea of Cortez — central (Loreto NP)
    { id: "isla-carmen",  name: "Isla Carmen",     lng: -111.18, lat: 26.02 },
    { id: "isla-danzante",name: "Isla Danzante",   lng: -111.27, lat: 25.78 },
    { id: "loreto",       name: "Loreto",          lng: -111.34, lat: 26.01 },
    // Mulegé / Santa Rosalía area
    { id: "isla-tortuga", name: "Isla Tortuga",    lng: -111.88, lat: 27.43 },
    { id: "isla-san-mar", name: "Isla San Marcos", lng: -112.10, lat: 27.21 },
    // Midriff Islands
    { id: "isla-tiburon", name: "Isla Tiburón",    lng: -112.40, lat: 29.00 },
    { id: "isla-ag",      name: "Ángel de la G.",  lng: -113.30, lat: 29.30 },
    { id: "isla-rasa",    name: "Isla Rasa",       lng: -112.97, lat: 28.83 },
    { id: "bahia-angel",  name: "Bahía Ángeles",   lng: -113.55, lat: 28.95 },
    // North Cortez
    { id: "roca-consag",  name: "Roca Consag",     lng: -114.48, lat: 31.10 },
    { id: "san-felipe",   name: "San Felipe",      lng: -114.83, lat: 31.03 },
  ],
};

export const SAVED_SPOTS =
  REGION_SAVED_SPOTS[activeRegion()] || REGION_SAVED_SPOTS.ca;

export const SST_STOPS = [
  { t: 0.00, c: [12, 38, 130] },
  { t: 0.25, c: [40, 130, 210] },
  { t: 0.50, c: [120, 220, 220] },
  { t: 0.70, c: [240, 220, 110] },
  { t: 0.85, c: [230, 110, 60] },
  { t: 1.00, c: [170, 20, 35] },
];
// Region-aware SST color range. Must match the per-region encoder
// override in pipeline/regions/{ca,pnw,tropical}.py
// (layer_range_overrides.sst). If the frontend used a fixed CA range
// against tropical data, the 25-32°C top end would saturate to flat
// red — same bug the user caught in QA.
const REGION_SST_RANGE = {
  ca:       [9,  25], // °C — legacy CA calibration
  pnw:      [5,  20], // °C — Olympic + Salish + OR outer coast
  tropical: [20, 32], // °C — Caribbean / Gulf / Florida year-round
  // Baja: PNG ENCODER is [14, 32] (matches baja.py layer_range_overrides;
  // covers Pacific upwelling floor + Cortez summer max). DISPLAY range
  // tightened to [20, 24] — a 4°C window centered on May's mean SST
  // (21.75°C from history summary). Pixel histogram on the live bundle
  // confirmed that with looser ranges (>50% of opaque cells render
  // deep navy / dark blue) the overlay was invisible against the
  // basemap blue at the default 62% opacity.
  //
  //   <20 °C  → saturates deep navy (Pacific upwelling, NorCal-current
  //             tongue at Cedros / Bahía Tortugas / Ensenada winter)
  //   20–22°C → blue → cyan (typical Pacific Baja + N Cortez spring)
  //   22–23°C → cyan → yellow (transition, mid-Cortez)
  //   23–24°C → yellow → orange (Cabo / La Paz summer)
  //   >24 °C  → saturates red (peak Cortez summer, Bahía Ángeles +
  //             San Felipe August surface)
  //
  // Trade-off: we lose nuance for cells outside [20,24]. Dive UX cares
  // about discriminating swim-comfortable bands (suit choice) more
  // than absolute extremes. Cursor pill still shows real °C/°F from
  // the manifest range (decode is independent of display range).
  baja:     [20, 24],
};
export const SST_RANGE =
  REGION_SST_RANGE[activeRegion()] || REGION_SST_RANGE.ca;

// ---- ΔT trend ramp (Phase A) -----------------------------------------
// Diverging palette for the 3-day SST trend view. Centered at 0 °C
// delta = neutral. Saturates at ±2 °C — a typical upwelling event
// shifts coastal SST by 1-2 °C within 3 days, so saturation falls just
// past the meaningful upper end and a "deep red plume / band of cold
// blue" reads at-a-glance rather than blending.
//
// Stops chosen to:
//   * keep ±0.2 °C visually neutral (under noise floor / averaging error)
//   * cool side: cyan → blue → deep navy (matches the existing SST cold
//     end so the visual idiom stays "cold = blue")
//   * warm side: warm orange → red (matches the SST hot end)
export const SST_TREND_RANGE_C = [-2.0, 2.0];   // saturation endpoints
export const SST_TREND_STOPS = [
  { d: -2.0, c: [12,  38, 130] },  // saturated cooling
  { d: -1.0, c: [40, 130, 210] },  // strong cooling
  { d: -0.4, c: [120, 200, 240] }, // mild cooling
  { d: -0.2, c: [220, 230, 240] }, // near-neutral cool
  { d:  0.0, c: [240, 240, 240] }, // neutral / under noise floor
  { d:  0.2, c: [240, 230, 220] }, // near-neutral warm
  { d:  0.4, c: [240, 200, 140] }, // mild warming
  { d:  1.0, c: [230, 110,  60] }, // strong warming
  { d:  2.0, c: [170,  20,  35] }, // saturated warming
];

export function sstTrendColor(deltaC) {
  if (!Number.isFinite(deltaC)) return "rgba(0,0,0,0)";
  // Clamp to the saturated endpoints — anything past ±2 °C ramps no further.
  const d = Math.max(SST_TREND_RANGE_C[0], Math.min(SST_TREND_RANGE_C[1], deltaC));
  for (let i = 0; i < SST_TREND_STOPS.length - 1; i++) {
    const a = SST_TREND_STOPS[i], b = SST_TREND_STOPS[i + 1];
    if (d >= a.d && d <= b.d) {
      const span = b.d - a.d;
      const t = span > 0 ? (d - a.d) / span : 0;
      const r = Math.round(a.c[0] + t * (b.c[0] - a.c[0]));
      const g = Math.round(a.c[1] + t * (b.c[1] - a.c[1]));
      const bl = Math.round(a.c[2] + t * (b.c[2] - a.c[2]));
      return `rgb(${r},${g},${bl})`;
    }
  }
  return "rgb(120,120,120)";
}

// Value-anchored chl stops (the v2 prototype). Anchored at the diving-
// relevant 0.1–5 mg/m³ range so the coastal upwelling band (~0.5–3 mg/m³)
// gets the full color spectrum instead of being squashed into the bottom
// 30% of a 0.05–20 log10 scale (which made every upwelling state look
// "navy" — see the 2026-05-03 user report).
//
// Stops are in mg/m³, lerped in log10 space (matches the underlying PNG
// encoding which is also log10):
//   0.10 → deep navy (Gin-clear oligotrophic)
//   0.30 → blue (still very clean)
//   0.80 → mid blue (typical clear nearshore)
//   2.00 → green (upwelling territory)
//   5.00 → warm brown (peak upwelling / mild bloom)
//
// Below 0.10 clamps to the deepest navy; above 5 clamps to brown.
// Open-ocean cells at 0.05–0.10 mg/m³ all render as one solid deep
// navy (correct: that IS gin-clear, no actionable variation), but the
// coastal 0.5–3 mg/m³ range now shows real differentiation.
export const CHL_STOPS = [
  { mg: 0.10, c: [31, 58, 85] },     // #1F3A55 Gin
  { mg: 0.30, c: [45, 84, 120] },    // #2D5478 Blue
  { mg: 0.80, c: [91, 141, 181] },   // #5B8DB5 Clear
  { mg: 2.00, c: [127, 160, 90] },   // #7FA05A Green
  { mg: 5.00, c: [122, 90, 60] },    // #7A5A3C Murky
];

const lerp = (a, b, t) => a + (b - a) * t;
const rgbStr = (c) => `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;

function rampColor(stops, t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1];
    if (t >= a.t && t <= b.t) {
      const k = (t - a.t) / (b.t - a.t);
      return [
        lerp(a.c[0], b.c[0], k),
        lerp(a.c[1], b.c[1], k),
        lerp(a.c[2], b.c[2], k),
      ];
    }
  }
  return stops[stops.length - 1].c;
}

export function sstColor(c) {
  const t = (c - SST_RANGE[0]) / (SST_RANGE[1] - SST_RANGE[0]);
  return rgbStr(rampColor(SST_STOPS, t));
}

export function chlColor(mg) {
  // Lerp directly across the value-anchored CHL_STOPS in log10 mg/m³ space.
  // Clamps below 0.10 to the navy floor and above 5.0 to the brown ceiling.
  if (!Number.isFinite(mg) || mg <= 0) return rgbStr(CHL_STOPS[0].c);
  if (mg <= CHL_STOPS[0].mg) return rgbStr(CHL_STOPS[0].c);
  const last = CHL_STOPS[CHL_STOPS.length - 1];
  if (mg >= last.mg) return rgbStr(last.c);
  for (let i = 0; i < CHL_STOPS.length - 1; i++) {
    const a = CHL_STOPS[i], b = CHL_STOPS[i + 1];
    if (mg >= a.mg && mg <= b.mg) {
      const k =
        (Math.log10(mg) - Math.log10(a.mg)) /
        (Math.log10(b.mg) - Math.log10(a.mg));
      return rgbStr([
        lerp(a.c[0], b.c[0], k),
        lerp(a.c[1], b.c[1], k),
        lerp(a.c[2], b.c[2], k),
      ]);
    }
  }
  return rgbStr(CHL_STOPS[0].c);
}

function hash(x, y, seed) {
  const h = Math.sin(x * 127.1 + y * 311.7 + seed * 13.31) * 43758.5453;
  return h - Math.floor(h);
}

function smoothNoise(x, y, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf);
  const v = yf * yf * (3 - 2 * yf);
  const a = hash(xi, yi, seed);
  const b = hash(xi + 1, yi, seed);
  const c = hash(xi, yi + 1, seed);
  const d = hash(xi + 1, yi + 1, seed);
  return lerp(lerp(a, b, u), lerp(c, d, u), v);
}

export function fbm(x, y, seed = 1) {
  let v = 0, amp = 0.5, freq = 1;
  for (let i = 0; i < 4; i++) {
    v += amp * smoothNoise(x * freq, y * freq, seed + i * 19);
    freq *= 2;
    amp *= 0.5;
  }
  return v;
}

// Simulated SST field for "mid-summer warm SoCal":
//   - Cool central coast (north & near-shore upwelling): 13–15°C
//   - Warm SoCal interior bight: 19–22°C
//   - Couple warm anomaly cells offshore SD/Catalina
export function sstAt(lng, lat) {
  let v = lerp(21.5, 13.5, (lat - 32.5) / (37.5 - 32.5));
  const coastDist = Math.min(Math.abs(lng - -121.5), Math.abs(lng - -120.8));
  if (lat > 34.5 && lat < 36.6) {
    v -= Math.max(0, 2.5 - coastDist * 4);
  }
  const bightX = lng - -118.7, bightY = lat - 33.6;
  const bightR2 = bightX * bightX * 0.7 + bightY * bightY * 1.2;
  v += Math.exp(-bightR2 * 1.5) * 2.2;
  const anomX = lng - -118.0, anomY = lat - 33.0;
  v += Math.exp(-(anomX * anomX + anomY * anomY) * 4) * 1.4;
  v += (fbm((lng + 124) * 1.2, (lat - 32) * 1.4, 7) - 0.5) * 1.8;
  return Math.max(9.5, Math.min(24.5, v));
}

export function chlAt(lng, lat) {
  let minD = 100;
  for (const [clng, clat] of COASTLINE) {
    const dx = lng - clng, dy = lat - clat;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < minD) minD = d;
  }
  let logv = Math.log10(0.1) + (1.1 / (minD * 4 + 0.6));
  const mb = Math.exp(-(((lng + 121.85) ** 2) * 8 + ((lat - 36.75) ** 2) * 8));
  logv += mb * 1.0;
  const pc = Math.exp(-(((lng + 120.6) ** 2) * 6 + ((lat - 34.55) ** 2) * 8));
  logv += pc * 0.55;
  logv += (fbm((lng + 124) * 1.6, (lat - 32) * 1.7, 13) - 0.5) * 0.8;
  const mg = Math.pow(10, logv);
  return Math.max(0.05, Math.min(20, mg));
}
