// Map projection helpers and mocked sea data.
// Bounding box: lat 31.8°N–37.6°N, lng -124.0° to -116.8° (extended south to
// include Las Islas Coronado and east to give Tijuana coast breathing room).

export const BBOX = { latMin: 31.8, latMax: 37.6, lngMin: -124.0, lngMax: -116.8 };

// Geographic aspect ratio (lng-degrees-as-distance / lat-degrees) at the
// bbox's mid latitude. Lng degrees shrink by cos(lat); we compute it once
// against the centre so 1 unit of x and y on the map represent the same
// distance on the ground. ~1.03 for our CA bbox — almost square.
const _MID_LAT_RAD = ((BBOX.latMin + BBOX.latMax) / 2) * Math.PI / 180;
const _COS_MID_LAT = Math.cos(_MID_LAT_RAD);
export const GEO_ASPECT =
  ((BBOX.lngMax - BBOX.lngMin) * _COS_MID_LAT) /
  (BBOX.latMax - BBOX.latMin);

// "Fill" mode for getFitted — when true, the bbox is stretched to fill
// the entire container instead of pillar/letterboxing to preserve the
// geographic aspect ratio. Set by App.jsx via setFillMode whenever
// useIsMobile() is true.
//
// Why mobile gets fill: on a portrait phone the CA bbox aspect (~1.24
// W:H) inside a phone container (~0.52 W:H) leaves huge top/bottom
// letterbox margins — about half the screen is empty cream/sky-blue.
// On desktop the side panels (Layer / Saved Spots / How to Read /
// Legend) fill the pillarbox margins, so contain mode looks fine.
//
// Trade-off: a slight vertical stretch (~2.4× on iPhone portrait)
// makes the coastline look "tall". Pin positions, MPAs, and the
// data overlay all stretch the same way (everything routes through
// getFitted), so they stay anchored to coastline features. For an
// at-a-glance ocean conditions viewer this is the right call.
let _fillMode = false;
export function setFillMode(fill) { _fillMode = !!fill; }
export function getFillMode() { return _fillMode; }

// Pillarbox / letterbox the bbox inside an arbitrary container so x and y
// pixels both represent the same on-the-ground distance regardless of the
// container's aspect ratio. Returns the rectangle inside (0..w, 0..h) that
// the geographic content should occupy.
export function getFitted(w, h) {
  if (!(w > 0) || !(h > 0)) {
    return { marginX: 0, marginY: 0, innerW: w || 0, innerH: h || 0 };
  }
  // Mobile fill mode: skip the aspect-preserving margin and use the
  // entire container. See _fillMode comment above for rationale.
  if (_fillMode) {
    return { marginX: 0, marginY: 0, innerW: w, innerH: h };
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

export const SAVED_SPOTS = [
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
];

export const SST_STOPS = [
  { t: 0.00, c: [12, 38, 130] },
  { t: 0.25, c: [40, 130, 210] },
  { t: 0.50, c: [120, 220, 220] },
  { t: 0.70, c: [240, 220, 110] },
  { t: 0.85, c: [230, 110, 60] },
  { t: 1.00, c: [170, 20, 35] },
];
export const SST_RANGE = [9, 25]; // °C

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
