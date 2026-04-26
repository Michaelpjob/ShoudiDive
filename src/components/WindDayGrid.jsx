import { useEffect, useState } from "react";
import {
  getWind5dSummary,
  loadWind5dHourly,
  hasWind5dHourly,
  windCardinal,
  bucketKey,
  hourKey,
} from "../lib/dataSource.js";

// Beaufort-aligned colour ramp — same kt stops as the legend / particles.
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

const BUCKET_META = {
  predawn:   { icon: "🌒", label: "Pre-dawn",  hours: "4–6 am"   },
  morning:   { icon: "🌅", label: "Morning",   hours: "6–10 am"  },
  midday:    { icon: "☀️", label: "Midday",    hours: "10 am–2 pm" },
  afternoon: { icon: "🌇", label: "Afternoon", hours: "2–7 pm"   },
  evening:   { icon: "🌙", label: "Evening",   hours: "7–9 pm"   },
};

// Direction arrow showing where the wind is going TO. dir is the meteorological
// "from" bearing in compass degrees, so we add 180° to flip.
function dirArrow(deg) {
  if (!Number.isFinite(deg)) return "·";
  const sectors = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"];
  const i = Math.round(((deg + 180) % 360) / 45) % 8;
  return sectors[i];
}

function RangeBar({ min, max }) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  const lo = Math.max(0, Math.min(min, 35));
  const hi = Math.max(0, Math.min(max, 35));
  const left  = (lo / 35) * 100;
  const right = (hi / 35) * 100;
  const width = Math.max(2, right - left);
  return (
    <div className="bucket-range">
      <div
        className="bucket-range-bar"
        style={{
          left: `${left}%`,
          width: `${width}%`,
          background: ktColor((lo + hi) / 2),
        }}
      />
    </div>
  );
}

function BucketCell({ bucket, isActive, onClick }) {
  const meta = BUCKET_META[bucket.bucket] || { icon: "·", label: bucket.bucket, hours: "" };
  const strong = bucket.mean_kt >= 18;
  const cardinal = Number.isFinite(bucket.mean_dir_deg) ? windCardinal(bucket.mean_dir_deg) : "—";
  return (
    <button
      type="button"
      className={`bucket-cell ${isActive ? "active" : ""} ${strong ? "warn" : ""}`}
      onClick={onClick}
      aria-label={`${meta.label}, ${bucket.mean_kt} knots from ${cardinal}`}
    >
      <span className="bucket-time">
        <span className="bucket-icon">{meta.icon}</span>
        <span className="bucket-label">{meta.label}</span>
        <span className="bucket-hours">{meta.hours}</span>
      </span>
      <span className="bucket-dir">
        <span className="bucket-arrow">{dirArrow(bucket.mean_dir_deg)}</span>
        <span className="bucket-cardinal">{cardinal}</span>
      </span>
      <span className="bucket-speed" style={{ color: ktColor(bucket.mean_kt) }}>
        {Number.isFinite(bucket.mean_kt) ? `${bucket.mean_kt.toFixed(1)} kt` : "—"}
      </span>
      <RangeBar min={bucket.min_kt} max={bucket.max_kt} />
      {strong && <span className="bucket-warn" title="Small craft advisory territory">⚠</span>}
    </button>
  );
}

function HourlyStrip({ day }) {
  const summary = getWind5dSummary();
  const dayInfo = summary?.days?.find((d) => d.day === day);
  const hours = Array.from({ length: 18 }, (_, i) => i + 4); // 4 am – 9 pm
  if (!dayInfo) return null;

  // Bucket-aggregate stats interpolated across hours so we have something
  // to show without waiting on the hourly fetch. Once `loadWind5dHourly`
  // resolves we'll have higher-resolution per-hour values; right now this
  // is just the bucket means painted across their hour range.
  const hourSpeed = new Array(24).fill(NaN);
  const hourDir = new Array(24).fill(NaN);
  for (const b of dayInfo.buckets || []) {
    const meta = BUCKET_META[b.bucket];
    const range = bucketHourRange(b.bucket);
    if (!range) continue;
    for (let h = range[0]; h < range[1]; h++) {
      hourSpeed[h] = b.mean_kt;
      hourDir[h] = b.mean_dir_deg;
    }
  }
  return (
    <div className="hourly-strip">
      <div className="hourly-row">
        {hours.map((h) => {
          const kt = hourSpeed[h];
          const isCalm = !Number.isFinite(kt) || kt < 1;
          return (
            <div className="hourly-cell" key={h}>
              <span className="hourly-time">{formatHour(h)}</span>
              <span className="hourly-bar-wrap">
                <span
                  className="hourly-bar"
                  style={{
                    height: Number.isFinite(kt) ? `${Math.min(100, (kt / 30) * 100)}%` : "0%",
                    background: ktColor(kt),
                  }}
                />
              </span>
              <span className="hourly-kt">{isCalm ? "·" : kt.toFixed(0)}</span>
              <span className="hourly-arrow">{dirArrow(hourDir[h])}</span>
            </div>
          );
        })}
      </div>
      <div className="hourly-note">
        Bucket means painted across each hour. Higher-resolution hourly model
        output will replace this when it loads.
      </div>
    </div>
  );
}

function bucketHourRange(bucket) {
  switch (bucket) {
    case "predawn":   return [4, 6];
    case "morning":   return [6, 10];
    case "midday":    return [10, 14];
    case "afternoon": return [14, 19];
    case "evening":   return [19, 21];
    default: return null;
  }
}

function formatHour(h) {
  if (h === 0) return "12a";
  if (h < 12) return `${h}a`;
  if (h === 12) return "12p";
  return `${h - 12}p`;
}

function fmtDate(iso) {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleString("en-US", { month: "short", day: "numeric" });
}

function DayCard({ day, sel, setSel, expanded, onToggleExpanded }) {
  const isActiveDay = sel.day === day.day;
  const confTier = day.confidence || "high";
  return (
    <div className={`wind-day-card conf-${confTier} ${isActiveDay ? "active" : ""}`}>
      <div className="day-header">
        <div className="day-header-text">
          <span className="day-weekday">{day.weekday}</span>
          <span className="day-date">{fmtDate(day.date)}</span>
        </div>
        {confTier !== "high" && (
          <span className="day-confidence" title="Forecast confidence drops past 2 days">
            {confTier === "medium" ? "~ medium" : "~ low"}
          </span>
        )}
      </div>
      <div className="bucket-stack">
        {(day.buckets || []).map((b) => (
          <BucketCell
            key={b.bucket}
            bucket={b}
            isActive={isActiveDay && sel.bucket === b.bucket && sel.hour == null}
            onClick={() =>
              setSel({ day: day.day, bucket: b.bucket, hour: null })
            }
          />
        ))}
        <div className="bucket-overnight">🌙 Calm overnight</div>
        <button
          type="button"
          className="hourly-toggle"
          onClick={onToggleExpanded}
          aria-expanded={expanded}
        >
          {expanded ? "Hide hourly forecast ↑" : "Hourly forecast ↓"}
        </button>
        {expanded && <HourlyStrip day={day.day} />}
      </div>
    </div>
  );
}

function BestWindow({ summary, onSelect }) {
  if (!summary?.best_window) return null;
  const { day, bucket, mean_kt } = summary.best_window;
  const dayInfo = summary.days?.find((d) => d.day === day);
  if (!dayInfo) return null;
  const meta = BUCKET_META[bucket] || { label: bucket };
  const friendly = (
    mean_kt < 5  ? "glassy" :
    mean_kt < 10 ? "light"  :
    mean_kt < 15 ? "moderate" :
    "windy"
  );
  return (
    <button
      type="button"
      className="wind-best-window"
      onClick={() => onSelect({ day, bucket, hour: null })}
      aria-label={`Best window: ${dayInfo.weekday} ${meta.label}, ${mean_kt} knots`}
    >
      <span className="best-dot" />
      <span className="best-text">
        Best window: <strong>{dayInfo.weekday} {meta.label}</strong>
        {" — "}{mean_kt} kt ({friendly})
      </span>
    </button>
  );
}

export default function WindDayGrid({ sel, setSel, layout = "stack" }) {
  const summary = getWind5dSummary();
  const [expandedDay, setExpandedDay] = useState(null);

  // Lazy-load the hourly grids for whichever day's drill-down is open.
  useEffect(() => {
    if (expandedDay == null) return;
    if (hasWind5dHourly(expandedDay)) return;
    loadWind5dHourly(expandedDay);
  }, [expandedDay]);

  if (!summary) {
    return (
      <div className="wind-day-grid empty">
        <p>5-day forecast not loaded yet.</p>
      </div>
    );
  }

  return (
    <div className={`wind-day-grid layout-${layout}`}>
      <BestWindow summary={summary} onSelect={setSel} />
      <div className="wind-days">
        {summary.days.map((d) => (
          <DayCard
            key={d.day}
            day={d}
            sel={sel}
            setSel={setSel}
            expanded={expandedDay === d.day}
            onToggleExpanded={() =>
              setExpandedDay((cur) => (cur === d.day ? null : d.day))
            }
          />
        ))}
      </div>
    </div>
  );
}

// Convenience helper: picks an initial selection. Priority:
//   1. summary.best_window (server-side glassiest pick)
//   2. First day with any bucket data → its first bucket
//   3. Last-resort hardcoded {0, morning} (lets the empty state still mount)
export function defaultWindSelection(summary) {
  if (summary?.best_window) {
    return {
      day:    summary.best_window.day,
      bucket: summary.best_window.bucket,
      hour:   null,
    };
  }
  for (const d of summary?.days || []) {
    if (d.buckets?.length) {
      return { day: d.day, bucket: d.buckets[0].bucket, hour: null };
    }
  }
  return { day: 0, bucket: "morning", hour: null };
}

// True iff the (day, bucket) combo exists in the loaded summary. Today's
// morning + pre-dawn buckets are dropped once they're in the past, so this
// has to be data-driven, not assumed-constant.
export function selectionHasData(summary, sel) {
  if (!summary || !sel) return false;
  const dayInfo = summary.days?.find((d) => d.day === sel.day);
  if (!dayInfo) return false;
  return dayInfo.buckets?.some((b) => b.bucket === sel.bucket) || false;
}

// Build a wind5d slot key from a selection. If the requested selection
// doesn't have data (e.g. today's morning is past), fall back to summary's
// best_window so the map always shows *something* meaningful instead of a
// blank no-data render.
export function selToSlotKey(sel, summary = null) {
  if (!sel) sel = defaultWindSelection(summary);
  if (summary && !selectionHasData(summary, sel) && sel.hour == null) {
    const fallback = defaultWindSelection(summary);
    if (fallback) sel = fallback;
  }
  if (sel.hour != null) return hourKey(sel.day, sel.hour);
  return bucketKey(sel.day, sel.bucket);
}
