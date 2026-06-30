// Navy Closures day-strip — the 7-day forecast selector for the SCI closures
// overlay. Distinct from the wind/swell/current scrubbers (it's an overlay, so
// it shows alongside whatever heatmap layer is active) and sits at the bottom
// of the map to avoid the top timelines. Reads the same singleton-loaded
// navy-closures.geojson as ClosuresLayer.

import { useEffect, useState } from "react";
import { loadClosures, isClosed } from "./ClosuresLayer.jsx";

function fmtDayOfMonth(iso) {
  return new Date(iso + "T12:00:00").getDate();
}
function weekday(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { weekday: "short" });
}

export default function ClosuresTimeline({ selectedDay, setSelectedDay, aboveScrubber }) {
  const [fc, setFc] = useState(null);
  useEffect(() => {
    let cancelled = false;
    loadClosures().then((d) => { if (!cancelled && d) setFc(d); });
    return () => { cancelled = true; };
  }, []);

  const dates = fc?.dates || [];
  if (!dates.length) return null;

  const closedCount = (di) =>
    (fc.features || []).filter(
      (f) => isClosed(f.properties.statusByDate?.[dates[di]]?.status)
    ).length;

  return (
    <div
      className={"closures-timeline" + (aboveScrubber ? " above-scrubber" : "")}
      role="group"
      aria-label="Navy closure 7-day forecast"
    >
      <div className="closures-tl-head">
        <span className="closures-tl-title">⚓ Navy closures</span>
        <span className="closures-tl-sub">San Clemente Island · tap a day</span>
      </div>
      <div className="closures-tl-days">
        {dates.map((iso, i) => {
          const n = closedCount(i);
          const sel = i === selectedDay;
          const today = i === 0;
          return (
            <button
              key={iso}
              type="button"
              className={
                "closures-day" +
                (sel ? " sel" : "") +
                (n > 0 ? " has-closure" : " clear")
              }
              aria-pressed={sel}
              onClick={(e) => { e.stopPropagation(); setSelectedDay(i); }}
            >
              <span className="cd-day">{today ? "Today" : weekday(iso)} {fmtDayOfMonth(iso)}</span>
              <span className="cd-tag">{n > 0 ? `${n} closed` : "clear"}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
