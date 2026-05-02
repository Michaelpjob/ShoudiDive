import 'dart:ui' show Color;

import 'theme/sid_tokens.dart';

/// Sample a palette at a value `v` expressed in the palette's user-facing
/// unit (kt for wind, ft for swell/vis, °F for sst, mg/m³ for chl).
/// Linear interpolation between adjacent stops; clamp at the ends.
/// Returns transparent for NaN.
Color _sample(List<SidStop> stops, double v) {
  if (v.isNaN) return const Color(0x00000000);
  if (v <= stops.first.value) return stops.first.color;
  if (v >= stops.last.value) return stops.last.color;
  for (int i = 0; i < stops.length - 1; i++) {
    final a = stops[i];
    final b = stops[i + 1];
    if (v >= a.value && v <= b.value) {
      final k = (v - a.value) / (b.value - a.value);
      return Color.lerp(a.color, b.color, k)!;
    }
  }
  return stops.last.color;
}

// Source data arrives in SI/metric (°C, m). Palettes are anchored in the
// units the user sees (°F, ft). Convert at the call site so the data
// pipeline upstream doesn't have to change.
double _cToF(double c) => c * 9.0 / 5.0 + 32.0;
double _mToFt(double m) => m * 3.28084;

Color sstColor(double celsius) =>
    _sample(SidPalettes.sst, celsius.isNaN ? double.nan : _cToF(celsius));

Color chlColor(double mg) => _sample(SidPalettes.chl, mg);

Color vizColor(double ft) => _sample(SidPalettes.vis, ft);

Color windColor(double kt) => _sample(SidPalettes.wind, kt);

Color swellColor(double hsM) =>
    _sample(SidPalettes.swell, hsM.isNaN ? double.nan : _mToFt(hsM));
