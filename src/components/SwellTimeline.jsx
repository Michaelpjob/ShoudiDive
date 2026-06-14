import { useEffect, useRef } from "react";
import {
  getSwell5dSummary,
  getSwell5dHourlyStats,
  getSwell5dStats,
  loadSwell5dHourly,
  windCardinal,
} from "../lib/dataSource.js";
import { pinnedSwell } from "../lib/pinSample.js";
import { bucketKey } from "../lib/loaders/decoders.js";
import { useTimelineDrag } from "./useTimelineDrag.js";

// Bucket → first hour (mirrors the wind timeline so a bucket-only
// selection has somewhere to plant the playhead).
const BUCKET_FIRST_HOUR = {
  predawn:    4,
  morning:    6,
  midday:    10,
  afternoon: 14,
  evening:   19,
};

function hourToBucket(h) {
  if (h >= 4  && h < 6)  return "predawn";
  if (h >= 6  && h < 10) return "morning";
  if (h >= 10 && h < 14) return "midday";
  if (h >= 14 && h < 19) return "afternoon";
  if (h >= 19 && h < 21) return "evening";
  return "evening";
}

function formatHour(h) {
  if (h === 0)  return "12a";
  if (h < 12)   return `${h}a`;
  if (h === 12) return "12p";
  return `${h - 12}p`;
}

function fmtDayOfMonth(iso) {
  const d = new Date(iso + "T12:00:00");
  return d.getDate();
}

// Direction arrow shows propagation (where it's GOING TO), even though the
// underlying Dp is "from" convention — matches the design doc.
function dirArrow(deg) {
  if (!Number.isFinite(deg)) return "";
  const sectors = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"];
  return sectors[Math.round(((deg + 180) % 360) / 45) % 8];
}

// Hs (m) → colour pill on the badge. Same gradient as the heatmap.
const HS_STOPS_M = [
  { hs: 0.0, c: "rgb(236,254,255)" },
  { hs: 0.3, c: "rgb(103,232,249)" },
  { hs: 1.0, c: "rgb(132,204,22)"  },
  { hs: 1.5, c: "rgb(234,179,8)"   },
  { hs: 2.5, c: "rgb(249,115,22)"  },
  { hs: 3.7, c: "rgb(220,38,38)"   },
  { hs: 6.0, c: "rgb(127,29,29)"   },
];
function hsColor(hsM) {
  if (!Number.isFinite(hsM)) return "var(--ink-3)";
  for (let i = 0; i < HS_STOPS_M.length - 1; i++) {
    const a = HS_STOPS_M[i], b = HS_STOPS_M[i + 1];
    if (hsM >= a.hs && hsM <= b.hs) return b.c;
  }
  return HS_STOPS_M[HS_STOPS_M.length - 1].c;
}

// Compact panel-side card mirroring WindCurrentSelectionCard but for swell.
// Big Hs readout, period + direction underneath, plus a confidence note.
export function SwellCurrentCard({ sel, hover }) {
  const summary = getSwell5dSummary();
  if (!summary) {
    return (
      <div className="wind-day-grid empty">
        <p>5-day swell forecast not loaded yet.</p>
      </div>
    );
  }
  const dayInfo = summary.days?.find((d) => d.day === sel?.day);
  const bucketName =
    sel?.hour != null ? hourToBucket(sel.hour) : sel?.bucket;
  const bucket = dayInfo?.buckets?.find((b) => b.bucket === bucketName);
  const stats = sel?.hour != null ? getSwell5dHourlyStats(sel.day, sel.hour) : null;
  // With a dropped pin, slave this card to THAT point instead of the region
  // mean (same helper the slider badge + map pin readout use).
  const pin =
    hover?.pinned && Number.isFinite(hover?.lng)
      ? pinnedSwell(hover.lng, hover.lat, sel)
      : null;
  const meanHsM  = pin ? pin.hs : stats?.hs ?? bucket?.mean_hs_m ?? null;
  const meanHsFt = Number.isFinite(meanHsM) ? meanHsM * 3.28084 : null;
  const meanTp   = pin ? pin.tp : stats?.tp ?? bucket?.mean_tp_s ?? null;
  const meanDp   = pin ? pin.dp : stats?.dp ?? bucket?.mean_dp_deg ?? null;
  const cardinal = Number.isFinite(meanDp) ? windCardinal(meanDp) : "—";

  // Period interpretation — drives the second-line description.
  const periodTag =
    !Number.isFinite(meanTp) ? null
    : meanTp >= 12 ? "long-period groundswell"
    : meanTp >= 8  ? "mixed swell"
    : "short-period windswell";

  // Time label
  const h12 = sel?.hour != null
    ? (sel.hour % 12 === 0 ? 12 : sel.hour % 12)
    : null;
  const ampm = sel?.hour != null ? (sel.hour < 12 ? "am" : "pm") : "";
  const timeLabel =
    sel?.hour != null && dayInfo
      ? `${dayInfo.weekday} ${h12} ${ampm}`
      : `${dayInfo?.weekday ?? ""} ${sel?.bucket ?? ""}`;

  return (
    <div className="wind-current-card">
      <div className="wind-current-stats">
        <div className="wcs-time">
          {timeLabel}
          {pin && <span className="wcs-atpin"> · at pin</span>}
        </div>
        <div className="wcs-kt-row">
          <span className="wcs-kt">
            {Number.isFinite(meanHsFt) ? meanHsFt.toFixed(1) : "—"}
            <span className="wcs-kt-unit"> ft</span>
          </span>
          <span className="wcs-dir">
            {Number.isFinite(meanTp) ? `${meanTp.toFixed(0)} s` : "—"}
            {Number.isFinite(meanDp) ? `  ·  ${dirArrow(meanDp)} ${cardinal}` : ""}
          </span>
        </div>
        {periodTag && (
          <div className="wcs-confidence" style={{ color: "var(--ink-3)", fontStyle: "normal" }}>
            {periodTag}
          </div>
        )}
        {/* Per-day confidence text removed 2026-05-24 — the horizon-aware
            confidence dot in the TopBar carries this signal now and updates
            as the user scrubs the timeline. */}
      </div>
    </div>
  );
}

export default function SwellTimeline({ sel, setSel, hover }) {
  const summary = getSwell5dSummary();
  const ref = useRef(null);

  useEffect(() => {
    if (!summary) return;
    for (const d of summary.days || []) {
      loadSwell5dHourly(d.day);
    }
  }, [summary]);

  // Compute slider math BEFORE the early return so React's hook order
  // stays stable across renders (see SstTimeline for the same dance).
  const numDays = summary?.days?.length || 0;
  const totalHours = numDays * 24;

  const selDay = Math.max(0, Math.min(numDays - 1, sel?.day ?? 0));
  const selHour =
    sel?.hour != null
      ? sel.hour
      : BUCKET_FIRST_HOUR[sel?.bucket] ?? 12;
  const currentHour = selDay * 24 + selHour;

  const drag = useTimelineDrag({
    ref,
    currentTarget: currentHour,
    xToTarget: (clientX) => {
      const r = ref.current?.getBoundingClientRect();
      if (!r || totalHours <= 1) return 0;
      const t = (clientX - r.left) / r.width;
      return Math.max(0, Math.min(totalHours - 1, Math.round(t * (totalHours - 1))));
    },
    targetToFrac: (h) => (totalHours > 1 ? h / (totalHours - 1) : 0),
    onCommit: (globalHour) => {
      const day = Math.floor(globalHour / 24);
      const hour = globalHour % 24;
      setSel({ day, bucket: hourToBucket(hour), hour });
    },
    step: (cur, delta) =>
      Math.max(0, Math.min(totalHours - 1, cur + delta)),
    totalSteps: Math.max(0, totalHours - 1),
  });

  if (!summary || !summary.days?.length) return null;

  const playheadFrac = drag.playheadFrac;

  // Real per-hour stats once the grid is loaded; bucket fallback while it
  // streams. Stats are in metric units (Hs m, Tp s, Dp deg from). We
  // convert Hs to feet at display time.
  const stats = getSwell5dHourlyStats(selDay, selHour);
  const dayInfo = summary.days[selDay];
  const bucketName = hourToBucket(selHour);
  const bucketStats = dayInfo?.buckets?.find((b) => b.bucket === bucketName);
  const displayHsM =
    stats && Number.isFinite(stats.hs)
      ? stats.hs
      : bucketStats?.mean_hs_m ?? null;
  const displayHsFt =
    Number.isFinite(displayHsM) ? displayHsM * 3.28084 : null;
  const displayTp =
    stats && Number.isFinite(stats.tp)
      ? stats.tp
      : bucketStats?.mean_tp_s ?? null;
  const regionDp =
    stats && Number.isFinite(stats.dp)
      ? stats.dp
      : bucketStats?.mean_dp_deg ?? null;

  // With a pin dropped, the playhead reports the swell AT THAT POINT through
  // the forecast (bucket grid, always loaded) instead of the area mean.
  const pinned = hover?.pinned && Number.isFinite(hover?.lng);
  const pinSw = pinned ? getSwell5dStats(hover.lng, hover.lat, bucketKey(selDay, bucketName)) : null;
  const atPin = pinned && pinSw && Number.isFinite(pinSw.hs);
  const showHsFt = atPin ? pinSw.hs * 3.28084 : displayHsFt;
  const showTp = atPin ? pinSw.tp : displayTp;
  const displayDp = atPin && Number.isFinite(pinSw.dp) ? pinSw.dp : regionDp;
  const isReal = stats != null;

  const dayCells = summary.days.map((d, i) => {
    const left = (i / numDays) * 100;
    const width = 100 / numDays;
    return (
      <div
        key={d.day}
        className={`tl-day-cell ${i % 2 === 0 ? "even" : "odd"} conf-${d.confidence}`}
        style={{ left: `${left}%`, width: `${width}%` }}
      >
        <span className="tl-day-label">
          {d.weekday.slice(0, 3)} {fmtDayOfMonth(d.date)}
        </span>
      </div>
    );
  });

  const ticks = [];
  for (let h = 0; h < totalHours; h++) {
    const isDayBoundary = h % 24 === 0;
    const isMajor = h % 6 === 0;
    const isMinor = h % 3 === 0;
    if (!isDayBoundary && !isMajor && !isMinor) continue;
    const left = (h / (totalHours - 1)) * 100;
    const klass = isDayBoundary ? "day" : isMajor ? "major" : "minor";
    ticks.push(
      <div
        key={h}
        className={`tl-tick ${klass}`}
        style={{ left: `${left}%` }}
      >
        {isMajor && !isDayBoundary && (
          <span className="tl-hour-label">{formatHour(h % 24)}</span>
        )}
      </div>
    );
  }

  const badgeClamp =
    playheadFrac < 0.1 ? "left" : playheadFrac > 0.9 ? "right" : "center";

  return (
    <div
      className={`wind-timeline ${drag.dragging ? "dragging" : ""}`}
      ref={ref}
      {...drag.handlers}
      role="slider"
      aria-label="Swell forecast time scrubber"
      aria-valuemin={0}
      aria-valuemax={totalHours - 1}
      aria-valuenow={currentHour}
    >
      <div className="tl-days">{dayCells}</div>
      <div className="tl-tickrow">{ticks}</div>
      <div
        className="tl-playhead"
        style={{ left: `${playheadFrac * 100}%` }}
      >
        <div className="tl-playhead-stem" />
        <div className={`tl-playhead-badge align-${badgeClamp}`}>
          <span className="tl-pb-time">
            {dayInfo?.weekday?.slice(0, 3)} {formatHour(selHour)}
            {atPin && <span className="tl-pb-atpin"> · at pin</span>}
          </span>
          {Number.isFinite(showHsFt) && (
            <span
              className="tl-pb-kt"
              style={{ background: hsColor(showHsFt / 3.28084) }}
            >
              {showHsFt.toFixed(1)} ft
            </span>
          )}
          {Number.isFinite(showTp) && (
            <span className="tl-pb-dir">
              {showTp.toFixed(0)} s
            </span>
          )}
          {Number.isFinite(displayDp) && (
            <span className="tl-pb-dir">
              {dirArrow(displayDp)} {windCardinal(displayDp)}
            </span>
          )}
          {!atPin && !isReal && Number.isFinite(displayHsFt) && (
            <span className="tl-pb-est" title="Bucket-mean estimate; per-hour grid still loading">
              ~
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
