import { useRef } from "react";
import { sstColor } from "../lib/mapData.js";
import {
  getSstHistoryStats,
  getSstHistorySummary,
} from "../lib/dataSource.js";
import { useTimelineDrag } from "./useTimelineDrag.js";

function fmtDay(iso) {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function fmtWeekday(iso) {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleString("en-US", {
    weekday: "short",
    timeZone: "UTC",
  });
}

function cToDisplay(c, units) {
  if (!Number.isFinite(c)) return null;
  return units === "F" ? c * 9 / 5 + 32 : c;
}

function unitLabel(units) {
  return units === "F" ? "F" : "C";
}

function deltaLabel(deltaC, units) {
  if (!Number.isFinite(deltaC) || Math.abs(deltaC) < 0.05) return "steady";
  const v = units === "F" ? deltaC * 9 / 5 : deltaC;
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)} deg ${unitLabel(units)}/day`;
}

function tempPillColor(c) {
  return Number.isFinite(c) ? sstColor(c) : "var(--ink-3)";
}

export function SstCurrentCard({ sel, units }) {
  const summary = getSstHistorySummary();
  if (!summary?.days?.length) {
    return (
      <div className="wind-day-grid empty">
        <p>Sea temperature history not loaded yet.</p>
      </div>
    );
  }

  const slot = sstSelToSlotKey(sel, summary);
  const day = getSstHistoryStats(slot);
  const idx = summary.days.findIndex((d) => d.slot === slot);
  const prev = idx > 0 ? summary.days[idx - 1] : null;
  const mean = cToDisplay(day?.mean, units);
  const min = cToDisplay(day?.min, units);
  const max = cToDisplay(day?.max, units);
  const deltaC =
    day && prev && Number.isFinite(day.mean) && Number.isFinite(prev.mean)
      ? day.mean - prev.mean
      : null;

  return (
    <div className="wind-current-card">
      <div className="wind-current-stats">
        <div className="wcs-time">
          {day?.date ? `${fmtWeekday(day.date)} ${fmtDay(day.date)}` : "Recent SST"}
        </div>
        <div className="wcs-kt-row">
          <span className="wcs-kt">
            {Number.isFinite(mean) ? mean.toFixed(1) : "--"}
            <span className="wcs-kt-unit"> deg {unitLabel(units)}</span>
          </span>
          <span className="wcs-dir">
            {Number.isFinite(min) && Number.isFinite(max)
              ? `${min.toFixed(0)}-${max.toFixed(0)} deg`
              : "range --"}
          </span>
        </div>
        <div className="wcs-confidence" style={{ color: "var(--ink-3)", fontStyle: "normal" }}>
          {deltaLabel(deltaC, units)}
        </div>
      </div>
    </div>
  );
}

export default function SstTimeline({ sel, setSel, units }) {
  const summary = getSstHistorySummary();
  const ref = useRef(null);

  // Hook lookup BEFORE the early return so React's hook order stays
  // stable across renders. We pass conservative defaults that the hook
  // tolerates when summary isn't ready yet.
  const days = summary?.days || [];
  const numDays = days.length;
  const slot = sstSelToSlotKey(sel, summary);
  const idx = Math.max(0, days.findIndex((d) => d.slot === slot));

  const drag = useTimelineDrag({
    ref,
    currentTarget: idx,
    xToTarget: (clientX) => {
      const r = ref.current?.getBoundingClientRect();
      if (!r || numDays <= 1) return 0;
      const t = (clientX - r.left) / r.width;
      return Math.max(0, Math.min(numDays - 1, Math.round(t * (numDays - 1))));
    },
    targetToFrac: (i) => (numDays > 1 ? i / (numDays - 1) : 0),
    onCommit: (nextIdx) => {
      const next = days[nextIdx];
      if (next) setSel({ slot: next.slot });
    },
    step: (cur, delta) =>
      Math.max(0, Math.min(numDays - 1, cur + delta)),
    totalSteps: Math.max(0, numDays - 1),
  });

  if (!summary?.days?.length) return null;

  const playheadFrac = drag.playheadFrac;
  const day = days[idx];
  const prev = idx > 0 ? days[idx - 1] : null;
  const mean = cToDisplay(day?.mean, units);
  const deltaC =
    day && prev && Number.isFinite(day.mean) && Number.isFinite(prev.mean)
      ? day.mean - prev.mean
      : null;

  const dayCells = days.map((d, i) => {
    const left = (i / numDays) * 100;
    const width = 100 / numDays;
    return (
      <div
        key={d.slot}
        className={`tl-day-cell ${i % 2 === 0 ? "even" : "odd"} conf-high`}
        style={{ left: `${left}%`, width: `${width}%` }}
      >
        <span className="tl-day-label">
          {fmtWeekday(d.date)} {fmtDay(d.date)}
        </span>
      </div>
    );
  });

  const ticks = days.map((d, i) => {
    const left = numDays > 1 ? (i / (numDays - 1)) * 100 : 0;
    return (
      <div
        key={d.slot}
        className={`tl-tick ${i === 0 ? "day" : "major"}`}
        style={{ left: `${left}%` }}
      />
    );
  });

  const badgeClamp =
    playheadFrac < 0.1 ? "left" : playheadFrac > 0.9 ? "right" : "center";

  return (
    <div
      className={`wind-timeline sst-timeline ${drag.dragging ? "dragging" : ""}`}
      ref={ref}
      {...drag.handlers}
      role="slider"
      aria-label="Sea temperature history scrubber"
      aria-valuemin={0}
      aria-valuemax={numDays - 1}
      aria-valuenow={idx}
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
            {day?.date ? fmtDay(day.date) : "SST"}
          </span>
          {Number.isFinite(mean) && (
            <span
              className="tl-pb-kt"
              style={{ background: tempPillColor(day.mean) }}
            >
              {mean.toFixed(1)} deg {unitLabel(units)}
            </span>
          )}
          <span className="tl-pb-dir">{deltaLabel(deltaC, units)}</span>
        </div>
      </div>
    </div>
  );
}

export function defaultSstSelection(summary) {
  const slot = summary?.latest_slot || summary?.days?.[summary.days.length - 1]?.slot;
  return { slot: slot || "d0" };
}

export function sstSelectionHasData(summary, sel) {
  if (!summary?.days?.length || !sel?.slot) return false;
  return summary.days.some((d) => d.slot === sel.slot);
}

export function sstSelToSlotKey(sel, summary = null) {
  if (summary && !sstSelectionHasData(summary, sel)) {
    return defaultSstSelection(summary).slot;
  }
  return sel?.slot || defaultSstSelection(summary).slot;
}
