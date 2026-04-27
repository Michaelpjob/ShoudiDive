// Color ramps + grayscale-PNG colorization for the mobile app.
//
// The pipeline encodes layer values as MODE='L' grayscale PNGs:
//   * px = 0          → "no data here", render transparent
//   * px = 1..255     → linear-or-log decode of the value within the
//                       layer's [lo, hi] range
//
// On the web, DataOverlay decodes pixel-by-pixel into a Float32Array
// and applies a colormap when painting to a canvas. On native we do
// the same thing — read the grayscale pixels via Skia, transform via
// the ramp, write a new RGBA Skia image, render that.
//
// Ramps must match the web's exactly so the look is consistent across
// surfaces. If you change one, change the other.

import { Skia, AlphaType, ColorType } from "@shopify/react-native-skia";


// ---- Ramps ----------------------------------------------------------

// SST: linear in 9–25 °C
export const SST_RANGE = [9, 25];
const SST_STOPS = [
  { t: 0.00, c: [12, 38, 130] },
  { t: 0.25, c: [40, 130, 210] },
  { t: 0.50, c: [120, 220, 220] },
  { t: 0.70, c: [240, 220, 110] },
  { t: 0.85, c: [230, 110, 60] },
  { t: 1.00, c: [170, 20, 35] },
];

// Chlorophyll: log10 in 0.05–20 mg/m³
const CHL_RANGE_LOG = [Math.log10(0.05), Math.log10(20)];
const CHL_STOPS = [
  { t: 0.00, c: [10, 50, 140] },
  { t: 0.25, c: [30, 130, 200] },
  { t: 0.50, c: [60, 200, 180] },
  { t: 0.75, c: [110, 210, 90] },
  { t: 1.00, c: [50, 130, 40] },
];

// Visibility: linear in feet. The pipeline writes the PNG with
// range [0, 80] ft.
export const VIZ_RANGE_FT = [0, 80];
const VIZ_STOPS_FT = [
  { v: 0,  c: [194, 65, 12]  },   // Poor      — burnt orange
  { v: 10, c: [234, 179, 8]  },   // Fair      — yellow
  { v: 20, c: [132, 204, 22] },   // Good      — lime
  { v: 30, c: [6, 182, 212]  },   // Very Good — cyan
  { v: 50, c: [3, 105, 161]  },   // Excellent — deep blue
];


function lerpRGB(a, b, k) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * k),
    Math.round(a[1] + (b[1] - a[1]) * k),
    Math.round(a[2] + (b[2] - a[2]) * k),
  ];
}


function rampLookup(stops, t, key = "t") {
  const tNorm = Math.max(stops[0][key], Math.min(stops[stops.length - 1][key], t));
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1];
    if (tNorm >= a[key] && tNorm <= b[key]) {
      const k = (tNorm - a[key]) / (b[key] - a[key]);
      return lerpRGB(a.c, b.c, k);
    }
  }
  return stops[stops.length - 1].c;
}


// Map a decoded layer value to an [r, g, b] triplet.
function valueToRGB(layer, value) {
  if (layer === "sst") {
    const t = (value - SST_RANGE[0]) / (SST_RANGE[1] - SST_RANGE[0]);
    return rampLookup(SST_STOPS, t);
  }
  if (layer === "chl") {
    if (value <= 0) return rampLookup(CHL_STOPS, 0);
    const t = (Math.log10(value) - CHL_RANGE_LOG[0]) /
              (CHL_RANGE_LOG[1] - CHL_RANGE_LOG[0]);
    return rampLookup(CHL_STOPS, t);
  }
  if (layer === "viz") {
    return rampLookup(VIZ_STOPS_FT, value, "v");
  }
  // Unknown layer — fall back to greyscale so it's obvious something
  // didn't get a ramp wired up.
  const g = Math.max(0, Math.min(255, value | 0));
  return [g, g, g];
}


// ---- Colorize a Skia grayscale image into a Skia RGBA image -------

/** Build a 256-entry lookup table for a layer so we colorize each
 * pixel by indexing instead of recomputing the ramp. The lookup
 * table maps `px` (0..255) → [r, g, b, a]. px=0 is "no data" and
 * is encoded transparent. */
function buildLut(layer) {
  const lut = new Uint8Array(256 * 4);
  // Layer-specific decoder for px → value
  let decode;
  if (layer === "sst") {
    const [lo, hi] = SST_RANGE;
    decode = (px) => lo + ((px - 1) / 254) * (hi - lo);
  } else if (layer === "chl") {
    const llo = CHL_RANGE_LOG[0], lhi = CHL_RANGE_LOG[1];
    decode = (px) => Math.pow(10, llo + ((px - 1) / 254) * (lhi - llo));
  } else if (layer === "viz") {
    const [lo, hi] = VIZ_RANGE_FT;
    decode = (px) => lo + ((px - 1) / 254) * (hi - lo);
  } else {
    decode = (px) => px;
  }
  for (let px = 0; px < 256; px++) {
    if (px === 0) {
      // No data → transparent
      lut[px * 4 + 0] = 0;
      lut[px * 4 + 1] = 0;
      lut[px * 4 + 2] = 0;
      lut[px * 4 + 3] = 0;
    } else {
      const v = decode(px);
      const [r, g, b] = valueToRGB(layer, v);
      lut[px * 4 + 0] = r;
      lut[px * 4 + 1] = g;
      lut[px * 4 + 2] = b;
      lut[px * 4 + 3] = 255;
    }
  }
  return lut;
}


/**
 * Read pixels from a grayscale SkImage, apply the layer's color ramp,
 * return a new RGBA SkImage. Caller should useMemo this so we only
 * recompute on layer / image change, not every frame.
 *
 * Returns null if the input image is null or the readPixels failed.
 */
export function colorizeImage(image, layer) {
  if (!image) return null;
  const w = image.width();
  const h = image.height();
  if (!w || !h) return null;

  const info = {
    width: w,
    height: h,
    colorType: ColorType.RGBA_8888,
    alphaType: AlphaType.Unpremul,
  };

  const src = image.readPixels(0, 0, info);
  if (!src) return null;
  // Skia's readPixels can return either a Uint8Array (Uint8ClampedArray
  // on web) or a typed-array-backed Buffer; normalise to Uint8Array.
  const srcBytes = src instanceof Uint8Array ? src : new Uint8Array(src);

  const lut = buildLut(layer);
  const dst = new Uint8Array(srcBytes.length);
  for (let i = 0; i < srcBytes.length; i += 4) {
    // Source PNG was mode='L', so R==G==B==value; sample R.
    const px = srcBytes[i];
    const o = px * 4;
    dst[i + 0] = lut[o + 0];
    dst[i + 1] = lut[o + 1];
    dst[i + 2] = lut[o + 2];
    dst[i + 3] = lut[o + 3];
  }

  // Wrap the colorized bytes as a SkData → SkImage. rowBytes = w * 4.
  const data = Skia.Data.fromBytes(dst);
  return Skia.Image.MakeImage(info, data, w * 4);
}
