import { useId, useMemo } from "react";

// Mean synodic-month length in days (NASA value: 29.530588853 days).
// Good to ~0.5 day over a few decades — fine for an icon-scale moon
// indicator. Use a JPL ephemeris if you ever need second-of-arc
// accuracy, but for "show me a crescent that matches tonight" this
// is plenty.
const SYNODIC_DAYS = 29.530588853;

// Reference new moon: 2000-01-06 18:14 UTC (well-documented epoch).
// JS Date.UTC months are 0-indexed.
const REF_NEW_MOON_MS = Date.UTC(2000, 0, 6, 18, 14);

// Hardcoded "moon" colors so the icon ALWAYS reads as a moon
// (dark night-side, warm cream lit-side) regardless of which theme
// is active around it. The surrounding card uses CSS theme vars.
const MOON_DARK = "#0F172A";    // night side
const MOON_LIT  = "#F5EDD8";    // warm cream
const MOON_RING = "rgba(245, 237, 216, 0.55)"; // cream @ ~55% — keeps silhouette visible at new moon

/** Returns the moon phase as a 0..1 fraction of the synodic month
 *  (0 = new, 0.5 = full). */
export function moonPhase(when) {
  const t = when instanceof Date ? when : new Date();
  const deltaDays = (t.getTime() - REF_NEW_MOON_MS) / 86400000;
  const p = (deltaDays / SYNODIC_DAYS) % 1;
  return p < 0 ? p + 1 : p;
}

/** 8-phase label. The 4 cardinal phases (new / quarters / full) are brief
 *  events, so they get NARROW windows (~±1.8 days); the crescent + gibbous
 *  bands fill the wide spans between. The old scheme gave each of the 8 an
 *  equal 1/8 band, which labelled a 38%-lit waxing moon "First quarter" (it's
 *  3 days short of the real quarter). This keeps the name consistent with the
 *  illumination — that moon now reads "Waxing crescent". */
export function moonPhaseName(phase) {
  if (phase < 0.03 || phase >= 0.97) return "New moon";
  if (phase < 0.22) return "Waxing crescent";
  if (phase < 0.28) return "First quarter";
  if (phase < 0.47) return "Waxing gibbous";
  if (phase < 0.53) return "Full moon";
  if (phase < 0.72) return "Waning gibbous";
  if (phase < 0.78) return "Last quarter";
  return "Waning crescent";
}

/** Fraction of disc illuminated, 0..1. New = 0, full = 1, quarters = 0.5. */
export function illuminationFraction(phase) {
  return (1 - Math.cos(phase * 2 * Math.PI)) / 2;
}

/** Days to next new moon (going forward from `phase`). */
export function daysToNextNew(phase) {
  return ((1 - phase) % 1) * SYNODIC_DAYS;
}

/** Days to next full moon (going forward from `phase`). */
export function daysToNextFull(phase) {
  // Full is at phase 0.5
  const delta = (0.5 - phase + 1) % 1;
  return delta * SYNODIC_DAYS;
}

/**
 * Just the moon disc as inline SVG. Used both inside MoonWidget and
 * standalone wherever a small moon indicator is needed.
 *
 * Renders dark/cream regardless of the surrounding theme so the
 * moon shape always reads correctly on both light and dark backgrounds.
 */
export default function MoonIcon({ date = null, size = 22 }) {
  const reactId = useId();
  const clipId = `moon-lit-${reactId.replace(/[^a-z0-9]/gi, "")}`;

  const phase = useMemo(() => moonPhase(date || new Date()), [date]);
  const phaseName = moonPhaseName(phase);

  const r = size / 2;
  const cx = r;
  const cy = r;

  const isNew = phase < 0.005 || phase > 0.995;
  const isWaxing = phase < 0.5;
  const phaseAngle = phase * 2 * Math.PI;
  const ellipseRx = (Math.abs(Math.cos(phaseAngle)) * 2 * r) / 2;
  const terminatorIsDark = phase < 0.25 || phase > 0.75;

  return (
    <span
      className="moon-icon"
      title={phaseName}
      aria-label={`Moon phase: ${phaseName}`}
      style={{ display: "inline-flex", lineHeight: 0 }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img">
        {!isNew && (
          <defs>
            <clipPath id={clipId}>
              <rect
                x={isWaxing ? cx : 0}
                y={0}
                width={r}
                height={size}
              />
            </clipPath>
          </defs>
        )}
        <circle cx={cx} cy={cy} r={r} fill={MOON_DARK} />
        {!isNew && (
          <>
            <circle cx={cx} cy={cy} r={r} fill={MOON_LIT} clipPath={`url(#${clipId})`} />
            <ellipse
              cx={cx}
              cy={cy}
              rx={ellipseRx}
              ry={r}
              fill={terminatorIsDark ? MOON_DARK : MOON_LIT}
            />
          </>
        )}
        {/* Cream outline so the silhouette stays visible at new moon
            on dark themes (where MOON_DARK blends into the bg). */}
        <circle
          cx={cx}
          cy={cy}
          r={r - 0.5}
          fill="none"
          stroke={MOON_RING}
          strokeWidth="0.8"
        />
      </svg>
    </span>
  );
}

/**
 * Floating card combining the moon icon with a label legend.
 * Designed to anchor in the map's upper-right corner via
 * `.moon-widget` positioning.
 *
 * Shows phase name, illumination %, and days to the next phase
 * milestone (new or full, whichever's closer). When `date` is a
 * scrubbed forecast time, also shows the date being displayed.
 */
export function MoonWidget({ date = null, className = "" }) {
  const isScrubbed = date instanceof Date;
  const t = date || new Date();
  const phase = useMemo(() => moonPhase(t), [t]);
  const phaseName = moonPhaseName(phase);
  const illum = Math.round(illuminationFraction(phase) * 100);

  // "X days to full" or "X days to new" — pick the closer milestone.
  const dToFull = daysToNextFull(phase);
  const dToNew = daysToNextNew(phase);
  const nextLabel = dToFull < dToNew
    ? `${Math.max(1, Math.round(dToFull))}d to full`
    : `${Math.max(1, Math.round(dToNew))}d to new`;
  // Suppress the "X days to" line when we're already at full or new
  // (within ~6 hours either side).
  const atMilestone = dToFull < 0.25 || dToNew < 0.25;

  const dateLabel = isScrubbed
    ? t.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
    : null;

  return (
    <div
      className={"moon-widget" + (className ? " " + className : "")}
      role="figure"
      aria-label={`Moon phase: ${phaseName}, ${illum}% illuminated`}
    >
      <div className="moon-widget-header">MOON</div>
      <div className="moon-widget-body">
        <MoonIcon date={t} size={36} />
        <div className="moon-widget-text">
          <div className="moon-widget-name">{phaseName}</div>
          <div className="moon-widget-meta">
            <span>{illum}% lit</span>
            {!atMilestone && <span className="dot-sep">·</span>}
            {!atMilestone && <span>{nextLabel}</span>}
          </div>
          {dateLabel && (
            <div className="moon-widget-date">{dateLabel}</div>
          )}
        </div>
      </div>
    </div>
  );
}
