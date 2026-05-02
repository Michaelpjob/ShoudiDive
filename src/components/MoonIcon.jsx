import { useId, useMemo } from "react";

// Mean synodic-month length in days (NASA value: 29.530588853 days).
// Good to ~0.5 day over a few decades — fine for an icon-scale moon
// indicator. Use a JPL ephemeris if you ever need second-of-arc
// accuracy, but for "show me a crescent that matches tonight" this
// is plenty.
const SYNODIC_DAYS = 29.530588853;

// Reference new moon: 2000-01-06 18:14 UTC (well-documented epoch).
// Note JS Date.UTC months are 0-indexed.
const REF_NEW_MOON_MS = Date.UTC(2000, 0, 6, 18, 14);

/**
 * Returns the moon phase as a fraction in [0, 1):
 *   0.00 — new moon (dark)
 *   0.25 — first quarter (right half lit, growing)
 *   0.50 — full moon (fully lit)
 *   0.75 — last quarter (left half lit, shrinking)
 *   1.00 — back to new moon
 *
 * Mirrors lib/moon_phase.dart in the Flutter app — keep in sync.
 */
export function moonPhase(when) {
  const t = when instanceof Date ? when : new Date();
  const deltaDays = (t.getTime() - REF_NEW_MOON_MS) / 86400000;
  const p = (deltaDays / SYNODIC_DAYS) % 1;
  return p < 0 ? p + 1 : p;
}

/** Standard 8-band phase label. */
export function moonPhaseName(phase) {
  if (phase < 1 / 16 || phase >= 15 / 16) return "New moon";
  if (phase < 3 / 16) return "Waxing crescent";
  if (phase < 5 / 16) return "First quarter";
  if (phase < 7 / 16) return "Waxing gibbous";
  if (phase < 9 / 16) return "Full moon";
  if (phase < 11 / 16) return "Waning gibbous";
  if (phase < 13 / 16) return "Last quarter";
  return "Waning crescent";
}

/**
 * Small disc rendering the moon phase at a given moment.
 *
 * `date` defaults to the current instant. Pass a forecast date
 * (e.g. the active wind/swell slot anchor) and the icon updates
 * to match that future date as the user scrubs the time slider.
 *
 * Rendering: a dark disc, then a lit semicircle on the
 * waxing/waning side (via clip-path), then an elliptical
 * terminator that either subtracts (crescent) or adds (gibbous)
 * lit area. Hairline ring inset slightly so the stroke isn't
 * clipped by the viewBox edge.
 */
export default function MoonIcon({ date = null, size = 22 }) {
  // Stable id for the SVG clip-path. useId prevents collisions
  // when multiple MoonIcons exist on the page.
  const reactId = useId();
  const clipId = `moon-lit-${reactId.replace(/[^a-z0-9]/gi, "")}`;

  const phase = useMemo(() => moonPhase(date || new Date()), [date]);
  const phaseName = moonPhaseName(phase);

  const r = size / 2;
  const cx = r;
  const cy = r;

  // CSS-var hooks so the moon picks up the active theme. Falls back
  // to the v2 ink/card hexes if the variables aren't defined.
  const dark = "var(--ink, #0f172a)";
  const lit = "var(--card, #ffffff)";
  const ring = "rgba(71, 85, 105, 0.30)"; // ink2 @ 30%

  // New-moon shortcut: pure dark disc + ring, no terminator math.
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

        {/* 1) Full dark disc */}
        <circle cx={cx} cy={cy} r={r} fill={dark} />

        {!isNew && (
          <>
            {/* 2) Lit semicircle on the appropriate side */}
            <circle
              cx={cx}
              cy={cy}
              r={r}
              fill={lit}
              clipPath={`url(#${clipId})`}
            />
            {/* 3) Elliptical terminator: dark for crescent, lit for gibbous */}
            <ellipse
              cx={cx}
              cy={cy}
              rx={ellipseRx}
              ry={r}
              fill={terminatorIsDark ? dark : lit}
            />
          </>
        )}

        {/* 4) Hairline ring inset to keep stroke inside the viewBox */}
        <circle
          cx={cx}
          cy={cy}
          r={r - 0.5}
          fill="none"
          stroke={ring}
          strokeWidth="0.8"
        />
      </svg>
    </span>
  );
}
