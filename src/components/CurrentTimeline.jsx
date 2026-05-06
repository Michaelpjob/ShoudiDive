import { useRef, useState } from "react";
import {
  bucketKey,
  currentSource,
  getCurrent5dSummary,
  windCardinal,
} from "../lib/dataSource.js";

const BUCKET_LABELS = {
  predawn: "Pre-dawn",
  morning: "Morning",
  midday: "Midday",
  afternoon: "Afternoon",
  evening: "Evening",
};

const CURRENT_STOPS = [
  { kt: 0.0, c: "rgb(125,211,252)" },
  { kt: 0.4, c: "rgb(125,211,252)" },
  { kt: 0.8, c: "rgb(94,234,212)"  },
  { kt: 1.2, c: "rgb(250,204,21)"  },
  { kt: 1.8, c: "rgb(249,115,22)"  },
  { kt: 2.5, c: "rgb(220,38,38)"   },
  { kt: 3.5, c: "rgb(126,34,206)"  },
];

function ktColor(kt) {
  if (!Number.isFinite(kt)) return "var(--ink-3)";
  for (let i = 0; i < CURRENT_STOPS.length - 1; i++) {
    const a = CURRENT_STOPS[i], b = CURRENT_STOPS[i + 1];
    if (kt >= a.kt && kt <= b.kt) return b.c;
  }
  return CURRENT_STOPS[CURRENT_STOPS.length - 1].c;
}

function fmtDayOfMonth(iso) {
  const d = new Date(iso + "T12:00:00");
  return d.getDate();
}

function flattenBuckets(summary) {
  const out = [];
  for (const day of summary?.days || []) {
    for (const b of day.buckets || []) {
      out.push({ day, bucket: b });
    }
  }
  return out;
}

function bucketLabel(bucket) {
  return BUCKET_LABELS[bucket] || bucket || "Window";
}

function sourceLabel(source) {
  if (source === "hfr_observed") return "HFR observed";
  if (source === "hfr_persistence_tide_wind") return "HFR + tide/wind";
  if (source === "inferred_tide_wind") return "tide/wind inferred";
  return source || "surface estimate";
}

function riskLabel(risk) {
  if (risk === "high") return "reversal risk high";
  if (risk === "medium") return "reversal risk medium";
  if (risk === "low") return "steady tide phase";
  return "reversal unknown";
}

function selectedIndex(items, sel) {
  const idx = items.findIndex(
    (x) => x.day.day === sel?.day && x.bucket.bucket === sel?.bucket
  );
  return idx >= 0 ? idx : 0;
}

export function CurrentCurrentCard({ sel }) {
  const summary = getCurrent5dSummary();
  if (!summary?.days?.length) {
    return (
      <div className="wind-day-grid empty">
        <p>Surface-current layer not loaded yet.</p>
      </div>
    );
  }
  const dayInfo = summary.days?.find((d) => d.day === sel?.day) || summary.days[0];
  const bucket = dayInfo?.buckets?.find((b) => b.bucket === sel?.bucket) || dayInfo?.buckets?.[0];
  const dir = Number.isFinite(bucket?.mean_dir_to_deg)
    ? windCardinal(bucket.mean_dir_to_deg)
    : null;
  return (
    <div className="wind-current-card">
      <div className="wind-current-stats">
        <div className="wcs-time">
          {dayInfo?.weekday ?? "Current"} {bucketLabel(bucket?.bucket)}
        </div>
        <div className="wcs-kt-row">
          <span className="wcs-kt">
            {Number.isFinite(bucket?.mean_kt) ? bucket.mean_kt.toFixed(1) : "--"}
            <span className="wcs-kt-unit"> kt</span>
          </span>
          <span className="wcs-dir">
            {dir ? `to ${dir}` : "direction --"}
          </span>
        </div>
        <div className="wcs-confidence" style={{ color: "var(--ink-3)", fontStyle: "normal" }}>
          {Number.isFinite(bucket?.consistency) ? `${bucket.consistency}% consistency` : "consistency --"}
          {" · "}
          {riskLabel(bucket?.reversal_risk)}
        </div>
        <div className="wcs-confidence">
          {sourceLabel(bucket?.source)}
          {summary.surface_note ? ` · ${summary.surface_note}` : ""}
        </div>
      </div>
    </div>
  );
}

export default function CurrentTimeline({ sel, setSel }) {
  const summary = getCurrent5dSummary();
  const ref = useRef(null);
  const [dragging, setDragging] = useState(false);

  if (!summary?.days?.length) return null;

  const items = flattenBuckets(summary);
  if (!items.length) return null;
  const idx = selectedIndex(items, sel);
  const current = items[idx];
  const playheadFrac = items.length > 1 ? idx / (items.length - 1) : 0;

  function xToIndex(clientX) {
    const r = ref.current.getBoundingClientRect();
    const t = (clientX - r.left) / r.width;
    return Math.max(0, Math.min(items.length - 1, Math.round(t * (items.length - 1))));
  }

  function setIndex(nextIdx) {
    const next = items[nextIdx];
    if (next) setSel({ day: next.day.day, bucket: next.bucket.bucket });
  }

  function onPointerDown(e) {
    setDragging(true);
    setIndex(xToIndex(e.clientX));
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }

  function onPointerMove(e) {
    if (!dragging) return;
    setIndex(xToIndex(e.clientX));
  }

  function onPointerUp(e) {
    setDragging(false);
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }

  const dayCells = summary.days.map((d, i) => {
    const left = (i / summary.days.length) * 100;
    const width = 100 / summary.days.length;
    return (
      <div
        key={d.day}
        className={`tl-day-cell ${i % 2 === 0 ? "even" : "odd"} conf-${d.confidence || "medium"}`}
        style={{ left: `${left}%`, width: `${width}%` }}
      >
        <span className="tl-day-label">
          {d.weekday.slice(0, 3)} {fmtDayOfMonth(d.date)}
        </span>
      </div>
    );
  });

  const ticks = items.map((item, i) => {
    const left = items.length > 1 ? (i / (items.length - 1)) * 100 : 0;
    const klass = item.bucket.bucket === "predawn" ? "day" : "major";
    return <div key={`${item.day.day}-${item.bucket.bucket}`} className={`tl-tick ${klass}`} style={{ left: `${left}%` }} />;
  });

  const bucket = current.bucket;
  const dir = Number.isFinite(bucket.mean_dir_to_deg)
    ? windCardinal(bucket.mean_dir_to_deg)
    : null;
  const badgeClamp =
    playheadFrac < 0.1 ? "left" : playheadFrac > 0.9 ? "right" : "center";

  return (
    <div
      className={`wind-timeline current-timeline ${dragging ? "dragging" : ""}`}
      ref={ref}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      role="slider"
      aria-label="Surface current time scrubber"
      aria-valuemin={0}
      aria-valuemax={items.length - 1}
      aria-valuenow={idx}
    >
      <div className="tl-days">{dayCells}</div>
      <div className="tl-tickrow">{ticks}</div>
      <div className="tl-playhead" style={{ left: `${playheadFrac * 100}%` }}>
        <div className="tl-playhead-stem" />
        <div className={`tl-playhead-badge align-${badgeClamp}`}>
          <span className="tl-pb-time">
            {current.day.weekday.slice(0, 3)} {bucketLabel(bucket.bucket)}
          </span>
          {Number.isFinite(bucket.mean_kt) && (
            <span className="tl-pb-kt" style={{ background: ktColor(bucket.mean_kt) }}>
              {bucket.mean_kt.toFixed(1)} kt
            </span>
          )}
          {dir && <span className="tl-pb-dir">to {dir}</span>}
          {Number.isFinite(bucket.consistency) && (
            <span className="tl-pb-dir">{bucket.consistency}% steady</span>
          )}
          <span className="tl-pb-est" title={sourceLabel(bucket.source)}>
            {bucket.source === "hfr_observed" ? "" : "~"}
          </span>
        </div>
      </div>
    </div>
  );
}

export function defaultCurrentSelection(summary) {
  const best = summary?.best_window;
  if (best?.bucket != null && best?.day != null) {
    return { day: best.day, bucket: best.bucket };
  }
  const first = summary?.days?.[0]?.buckets?.[0];
  return { day: 0, bucket: first?.bucket || "midday" };
}

export function currentSelectionHasData(summary, sel) {
  if (!summary?.days?.length || sel?.day == null || !sel?.bucket) return false;
  const day = summary.days.find((d) => d.day === sel.day);
  return Boolean(day?.buckets?.some((b) => b.bucket === sel.bucket));
}

export function currentSelToSlotKey(sel, summary = null) {
  if (summary && !currentSelectionHasData(summary, sel)) {
    const fallback = defaultCurrentSelection(summary);
    return bucketKey(fallback.day, fallback.bucket);
  }
  return bucketKey(sel?.day ?? 0, sel?.bucket || "midday");
}

export function currentSelectionSource(sel, summary = null) {
  const slot = currentSelToSlotKey(sel, summary);
  return currentSource(slot);
}
