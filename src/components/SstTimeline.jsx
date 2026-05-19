import { useRef } from "react";
import { sstColor } from "../lib/mapData.js";
import {
  getSstForecastStats,
  getSstForecastSummary,
  getSstHistoryStats,
  getSstHistorySummary,
} from "../lib/dataSource.js";
import { findTodayDay } from "../lib/today.js";
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

function summaryForMode(mode) {
  return mode === "forecast" ? getSstForecastSummary() : getSstHistorySummary();
}

function statsForMode(mode, slot) {
  return mode === "forecast" ? getSstForecastStats(slot) : getSstHistoryStats(slot);
}

export function SstModeToggle({ mode, setMode, hasHistory = true, hasForecast = false }) {
  return (
    <div className="sst-mode-toggle" role="tablist" aria-label="Temperature time mode">
      <button
        type="button"
        className={mode !== "forecast" ? "active" : ""}
        onClick={() => setMode("history")}
        disabled={!hasHistory}
      >
        History
      </button>
      <button
        type="button"
        className={mode === "forecast" ? "active" : ""}
        onClick={() => setMode("forecast")}
        disabled={!hasForecast}
      >
        Forecast
        <span>Beta</span>
      </button>
    </div>
  );
}

export function SstCurrentCard({ sel, units, mode = "history" }) {
  const summary = summaryForMode(mode);
  if (!summary?.days?.length) {
    return (
      <div className="wind-day-grid empty">
        <p>Sea temperature {mode === "forecast" ? "forecast" : "history"} not loaded yet.</p>
      </div>
    );
  }

  const slot = sstSelToSlotKey(sel, summary);
  const day = statsForMode(mode, slot);
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
          {day?.date ? `${fmtWeekday(day.date)} ${fmtDay(day.date)}` : "SST"}
          {mode === "forecast" && <span className="sst-beta-pill">Beta</span>}
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
          {mode === "forecast"
            ? `${day?.confidence || "low"} confidence trend model`
            : deltaLabel(deltaC, units)}
        </div>
      </div>
    </div>
  );
}

export default function SstTimeline({ sel, setSel, units, mode = "history" }) {
  const summary = summaryForMode(mode);
  const ref = useRef(null);

  // Hook lookup BEFORE the early return so React's hook order stays
  // stable across renders. We pass conservative defaults that the hook
  // tolerates when summary isn't ready yet.
  const days = summary?.days || [];
  const numDays = days.length;
  const slot = sstSelToSlotKey(sel, summary);
  // Match the same effectiveSlot resolution used by the helpers above
  // so legacy summaries (no per-day .slot field) still position the
  // playhead correctly when slot keys are synthesized as f<offset>.
  const idx = Math.max(0, days.findIndex((d, i) => effectiveSlot(d, i) === slot));

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
      if (next) setSel({ slot: effectiveSlot(next, nextIdx) });
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
        key={effectiveSlot(d, i)}
        className={`tl-day-cell ${i % 2 === 0 ? "even" : "odd"} conf-${d.confidence || "high"}`}
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
        key={effectiveSlot(d, i)}
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
      aria-label={`Sea temperature ${mode === "forecast" ? "forecast" : "history"} scrubber`}
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
          {mode === "forecast" && <span className="sst-beta-pill">Beta</span>}
          {Number.isFinite(mean) && (
            <span
              className="tl-pb-kt"
              style={{ background: tempPillColor(day.mean) }}
            >
              {mean.toFixed(1)} deg {unitLabel(units)}
            </span>
          )}
          <span className="tl-pb-dir">
            {mode === "forecast" ? `${day?.confidence || "low"} confidence` : deltaLabel(deltaC, units)}
          </span>
        </div>
      </div>
    </div>
  );
}

// Tolerate summaries that omit the `slot` field on each day (legacy
// fetch_sst_5day.py emissions before the 2026-05-09 fix). The loader
// in src/lib/loaders/sst5d.js synthesizes `f<offset>` keys when the
// slot is missing — the timeline must use the same synthesis so the
// slider's `days` array references the same keys the loader wrote.
//
// Resolution order (must match loaders/sst5d.js):
//   explicit d.slot  →  "f<offset>" if offset is an integer  →  "f<idx>"
function effectiveSlot(d, idx) {
  if (d?.slot) return d.slot;
  if (Number.isInteger(d?.offset)) return `f${d.offset}`;
  return `f${idx}`;
}

export function defaultSstSelection(summary) {
  const days = summary?.days || [];
  // Server-pinned default wins (typically "f0" today on sst5d).
  const explicit = summary?.default_slot || summary?.latest_slot;
  if (explicit) return { slot: explicit };
  if (days.length === 0) return { slot: "d0" };
  // Prefer "today" (date match in summary tz) so sst5d forecasts
  // open on today, not on the +5 day far-edge.
  const today = findTodayDay(summary);
  if (today) {
    const idx = days.indexOf(today);
    return { slot: effectiveSlot(today, idx) };
  }
  // Last-resort: most-recent observation for history, furthest-out
  // forecast for sst5d. Same as previous behaviour.
  const last = days[days.length - 1];
  return { slot: effectiveSlot(last, days.length - 1) };
}

export function sstSelectionHasData(summary, sel) {
  if (!summary?.days?.length || !sel?.slot) return false;
  return summary.days.some((d, idx) => effectiveSlot(d, idx) === sel.slot);
}

export function sstSelToSlotKey(sel, summary = null) {
  if (summary && !sstSelectionHasData(summary, sel)) {
    return defaultSstSelection(summary).slot;
  }
  return sel?.slot || defaultSstSelection(summary).slot;
}
