// Single source of truth for "what's the condition at the dropped pin, for the
// current timeline selection." Three places used to answer this question three
// different ways — the slider playhead badge sampled the bucket-mean grid, the
// map pin readout sampled whatever slot the raster was painting (hourly once a
// day's hourly grids streamed in), and the left-rail forecast card ignored the
// pin entirely and showed the REGION mean. Result: a pin could read 16 kt on
// the map, 16 kt on the slider, but 11 kt on the "forecast" card.
//
// These helpers sample the SAME slot the map raster paints, at the pin's
// lng/lat, so all three readouts report one number. Hourly is preferred and
// falls back to the bucket mean while that hour's grid is still loading, so the
// value tracks the map AND never blanks mid-scrub. `real` is false on the
// bucket-mean fallback so callers can flag it as an estimate.
import {
  getWind5dSpeed,
  getWind5dUV,
  getCurrentSpeed,
  getCurrentUV,
  getSwell5dStats,
  windCompass,
} from "./dataSource.js";

function bucketForHour(h) {
  if (h >= 4 && h < 6) return "predawn";
  if (h >= 6 && h < 10) return "morning";
  if (h >= 10 && h < 14) return "midday";
  if (h >= 14 && h < 19) return "afternoon";
  return "evening";
}

// Resolve { hourlySlot|null, bucketSlot } for a timeline selection. Bucket-only
// selections (sel.hour == null) have no hourly slot. Mirrors selToSlotKey() in
// WindDayGrid.jsx, kept inline here to avoid a component→lib import cycle.
function slots(sel) {
  const day = sel?.day ?? 0;
  const bucket = sel?.hour != null ? bucketForHour(sel.hour) : sel?.bucket || "midday";
  const bucketSlot = `d${day}_${bucket}`;
  const hourlySlot =
    sel?.hour != null ? `d${day}_h${String(sel.hour).padStart(2, "0")}` : null;
  return { hourlySlot, bucketSlot };
}

export function pinnedWind(lng, lat, sel) {
  if (!Number.isFinite(lng) || !sel) return null;
  const { hourlySlot, bucketSlot } = slots(sel);
  let kt = NaN;
  let uv = null;
  let real = false;
  if (hourlySlot) {
    const hk = getWind5dSpeed(lng, lat, hourlySlot);
    if (Number.isFinite(hk)) {
      kt = hk;
      uv = getWind5dUV(lng, lat, hourlySlot);
      real = true;
    }
  }
  if (!real) {
    kt = getWind5dSpeed(lng, lat, bucketSlot);
    uv = getWind5dUV(lng, lat, bucketSlot);
  }
  if (!Number.isFinite(kt)) return null;
  const dir =
    uv && Number.isFinite(uv.u) && Number.isFinite(uv.v)
      ? windCompass(uv.u, uv.v)
      : null;
  return { kt, dir, real };
}

export function pinnedCurrent(lng, lat, sel) {
  if (!Number.isFinite(lng) || !sel) return null;
  // current5d publishes bucket grids only — no per-hour grids to prefer.
  const { bucketSlot } = slots(sel);
  const kt = getCurrentSpeed(lng, lat, bucketSlot);
  if (!Number.isFinite(kt)) return null;
  const uv = getCurrentUV(lng, lat, bucketSlot);
  const dirToDeg =
    uv && Number.isFinite(uv.u) && Number.isFinite(uv.v)
      ? (Math.atan2(uv.u, uv.v) * 180 / Math.PI + 360) % 360
      : null;
  return { kt, dirToDeg, real: true };
}

export function pinnedSwell(lng, lat, sel) {
  if (!Number.isFinite(lng) || !sel) return null;
  const { hourlySlot, bucketSlot } = slots(sel);
  let w = null;
  let real = false;
  if (hourlySlot) {
    const hw = getSwell5dStats(lng, lat, hourlySlot);
    if (hw && Number.isFinite(hw.hs)) {
      w = hw;
      real = true;
    }
  }
  if (!real) w = getSwell5dStats(lng, lat, bucketSlot);
  if (!w || !Number.isFinite(w.hs)) return null;
  return { hs: w.hs, tp: w.tp, dp: w.dp, real };
}
