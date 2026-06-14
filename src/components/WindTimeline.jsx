import { useEffect, useRef } from "react";
import {
  getWind5dSummary,
  getWind5dHourlyStats,
  loadWind5dHourly,
  windCardinal,
} from "../lib/dataSource.js";
import { pinnedWind } from "../lib/pinSample.js";
import { useTimelineDrag } from "./useTimelineDrag.js";

// Bucket → first hour of that bucket (for selections that came from a card click).
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

function dirArrow(deg) {
  if (!Number.isFinite(deg)) return "";
  // Wind direction-from → arrow pointing where it's GOING TO (rotate 180°).
  const sectors = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"];
  return sectors[Math.round(((deg + 180) % 360) / 45) % 8];
}

// Beaufort-aligned colour ramp for the speed pill on the badge.
const KT_STOPS = [
  { kt: 0,  c: "rgb(170,210,240)" },
  { kt: 5,  c: "rgb(170,210,240)" },
  { kt: 10, c: "rgb(120,200,160)" },
  { kt: 15, c: "rgb(220,220,100)" },
  { kt: 20, c: "rgb(240,160,70)"  },
  { kt: 25, c: "rgb(220,90,60)"   },
  { kt: 35, c: "rgb(140,30,90)"   },
];
function ktColor(kt) {
  if (!Number.isFinite(kt)) return "var(--ink-3)";
  for (let i = 0; i < KT_STOPS.length - 1; i++) {
    const a = KT_STOPS[i], b = KT_STOPS[i + 1];
    if (kt >= a.kt && kt <= b.kt) return b.c;
  }
  return KT_STOPS[KT_STOPS.length - 1].c;
}

export default function WindTimeline({ sel, setSel, hover }) {
  const summary = getWind5dSummary();
  const ref = useRef(null);

  // Background-load every day's hourly grids so scrubbing across the
  // timeline doesn't reveal placeholder cells. Idempotent + dedupes.
  useEffect(() => {
    if (!summary) return;
    for (const d of summary.days || []) {
      loadWind5dHourly(d.day);
    }
  }, [summary]);

  // Compute slider math BEFORE any conditional return so React's hook
  // order stays stable across renders (the useTimelineDrag hook below
  // would otherwise see different call counts on first render vs after
  // the summary lands).
  const numDays = summary?.days?.length || 0;
  const totalHours = numDays * 24;

  // Convert sel → global hour offset (0..120). Bucket-only selections snap
  // to the bucket's first hour so the playhead has somewhere to sit.
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
    // Keyboard: ←/→ steps one hour, PageUp/PageDown jumps a full day,
    // Home/End jumps to the first/last hour.
    step: (cur, delta) =>
      Math.max(0, Math.min(totalHours - 1, cur + delta)),
    totalSteps: Math.max(0, totalHours - 1),
  });

  if (!summary || !summary.days?.length) return null;

  const playheadFrac = drag.playheadFrac;

  // Stats for the playhead badge — real per-hour if loaded, otherwise the
  // bucket's mean.
  const stats = getWind5dHourlyStats(selDay, selHour);
  const dayInfo = summary.days[selDay];
  const bucketName = hourToBucket(selHour);
  const bucketStats = dayInfo?.buckets?.find((b) => b.bucket === bucketName);
  const regionKt =
    stats && Number.isFinite(stats.kt)
      ? stats.kt
      : bucketStats?.mean_kt ?? null;
  const regionDir =
    stats && Number.isFinite(stats.dir)
      ? stats.dir
      : bucketStats?.mean_dir_deg ?? null;

  // When a pin is dropped, the playhead reports THAT location through time
  // instead of the area mean — so scrubbing answers "what's the wind at my
  // spot". pinnedWind() samples the SAME slot the map raster paints (the
  // scrubbed hour once its grid is in, the bucket mean while it loads), so the
  // badge agrees to the decimal with the map pin readout and the left forecast
  // card. (.real is false on the bucket-mean fallback → flagged as an estimate.)
  const pinned = hover?.pinned && Number.isFinite(hover?.lng);
  const pinSample = pinned ? pinnedWind(hover.lng, hover.lat, sel) : null;
  const pinKt = pinSample ? pinSample.kt : null;
  const pinDir = pinSample ? pinSample.dir : null;
  const atPin = pinned && Number.isFinite(pinKt);

  const displayKt = atPin ? pinKt : regionKt;
  const displayDir = atPin && Number.isFinite(pinDir) ? pinDir : regionDir;
  const isReal = atPin ? pinSample.real : stats != null;

  // Day stripes — alternating bg so the boundaries are visible at a glance.
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

  // Tick marks. Major at 6-hour boundaries, day boundary draws a heavier line.
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

  // Edge clamp so the badge never clips off-screen.
  const badgeClamp =
    playheadFrac < 0.1 ? "left" : playheadFrac > 0.9 ? "right" : "center";

  return (
    <div
      className={`wind-timeline ${drag.dragging ? "dragging" : ""}`}
      ref={ref}
      {...drag.handlers}
      role="slider"
      aria-label="Wind forecast time scrubber"
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
          {Number.isFinite(displayKt) && (
            <span
              className="tl-pb-kt"
              style={{ background: ktColor(displayKt) }}
            >
              {displayKt.toFixed(1)} kt
            </span>
          )}
          {Number.isFinite(displayDir) && (
            <span className="tl-pb-dir">
              {dirArrow(displayDir)} {windCardinal(displayDir)}
            </span>
          )}
          {!isReal && Number.isFinite(displayKt) && (
            <span className="tl-pb-est" title="Bucket-mean estimate; per-hour grid still loading">
              ~
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
