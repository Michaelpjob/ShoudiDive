import 'dart:math' as math;

/// Mean synodic-month length in days (NASA value: 29.530588853 days).
/// Good to ±0.5 day over a few decades — fine for an icon-scale moon
/// indicator. Use a JPL ephemeris if you ever need second-of-arc
/// accuracy, but for "show me a crescent that matches tonight" this
/// is plenty.
const double _synodicMonthDays = 29.530588853;

/// Well-documented reference new moon: 2000-01-06 18:14 UTC.
final DateTime _referenceNewMoonUtc = DateTime.utc(2000, 1, 6, 18, 14);

/// Returns phase as a fraction in [0.0, 1.0):
///
///   0.00 — new moon (dark)
///   0.25 — first quarter (right half lit, growing)
///   0.50 — full moon (fully lit)
///   0.75 — last quarter (left half lit, shrinking)
///   1.00 — back to new moon
///
/// `when` is interpreted in UTC. A naive DateTime is assumed to be
/// local time and converted; the phase is well-defined to within
/// ~6 hours of midnight in any time zone, so the conversion fuzziness
/// doesn't change the visible icon.
double moonPhase(DateTime when) {
  final w = when.isUtc ? when : when.toUtc();
  final deltaDays = w.difference(_referenceNewMoonUtc).inMicroseconds /
      Duration.microsecondsPerDay;
  final p = (deltaDays / _synodicMonthDays) % 1.0;
  return p < 0 ? p + 1.0 : p;
}

/// Fraction of the disk that's illuminated, 0.0 to 1.0.
/// Useful if you want a single number rather than a phase angle:
///
///   new moon       → 0.0
///   first quarter  → 0.5
///   full moon      → 1.0
///   last quarter   → 0.5
double illuminationFraction(double phase) {
  // Half-cycle illumination function: sinusoidal between 0 and 1.
  // illum = (1 - cos(phase * 2π)) / 2
  return (1.0 - math.cos(phase * 2 * math.pi)) / 2.0;
}

/// Human label for a phase fraction. Bands are 1/16th of a cycle
/// either side of the canonical phase angles, matching the standard
/// 8-phase set most apps use.
String moonPhaseName(double phase) {
  if (phase < 1 / 16 || phase >= 15 / 16) return 'New moon';
  if (phase < 3 / 16) return 'Waxing crescent';
  if (phase < 5 / 16) return 'First quarter';
  if (phase < 7 / 16) return 'Waxing gibbous';
  if (phase < 9 / 16) return 'Full moon';
  if (phase < 11 / 16) return 'Waning gibbous';
  if (phase < 13 / 16) return 'Last quarter';
  return 'Waning crescent';
}
