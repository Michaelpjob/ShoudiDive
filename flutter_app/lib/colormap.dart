import 'dart:math' as math;
import 'dart:ui' show Color;

class ColorStop {
  final double t;
  final int r;
  final int g;
  final int b;
  const ColorStop(this.t, this.r, this.g, this.b);
}

class Colormap {
  final List<ColorStop> stops;
  const Colormap._(this.stops);

  Color sample(double tIn) {
    if (tIn.isNaN) return const Color(0x00000000);
    final t = tIn.clamp(0.0, 1.0);
    for (int i = 0; i < stops.length - 1; i++) {
      final a = stops[i];
      final b = stops[i + 1];
      if (t >= a.t && t <= b.t) {
        final k = (t - a.t) / (b.t - a.t);
        return Color.fromARGB(
          255,
          (a.r + (b.r - a.r) * k).round(),
          (a.g + (b.g - a.g) * k).round(),
          (a.b + (b.b - a.b) * k).round(),
        );
      }
    }
    final last = stops.last;
    return Color.fromARGB(255, last.r, last.g, last.b);
  }
}

const Colormap kSstRamp = Colormap._([
  ColorStop(0.00, 12, 38, 130),
  ColorStop(0.25, 40, 130, 210),
  ColorStop(0.50, 120, 220, 220),
  ColorStop(0.70, 240, 220, 110),
  ColorStop(0.85, 230, 110, 60),
  ColorStop(1.00, 170, 20, 35),
]);

const Colormap kChlRamp = Colormap._([
  ColorStop(0.00, 10, 50, 140),
  ColorStop(0.25, 30, 130, 200),
  ColorStop(0.50, 60, 200, 180),
  ColorStop(0.75, 110, 210, 90),
  ColorStop(1.00, 50, 130, 40),
]);

class _BandStop {
  final double v;
  final int r;
  final int g;
  final int b;
  const _BandStop(this.v, this.r, this.g, this.b);
}

const List<_BandStop> _vizRamp = [
  _BandStop(0, 194, 65, 12),
  _BandStop(10, 234, 179, 8),
  _BandStop(20, 132, 204, 22),
  _BandStop(30, 6, 182, 212),
  _BandStop(50, 3, 105, 161),
];

const List<_BandStop> _windRamp = [
  _BandStop(0, 230, 240, 250),
  _BandStop(5, 170, 210, 240),
  _BandStop(10, 120, 200, 160),
  _BandStop(15, 220, 220, 100),
  _BandStop(20, 240, 160, 70),
  _BandStop(25, 220, 90, 60),
  _BandStop(35, 140, 30, 90),
];

const List<_BandStop> _swellRampM = [
  _BandStop(0.0, 236, 254, 255),
  _BandStop(0.3, 103, 232, 249),
  _BandStop(1.0, 132, 204, 22),
  _BandStop(1.5, 234, 179, 8),
  _BandStop(2.5, 249, 115, 22),
  _BandStop(3.7, 220, 38, 38),
  _BandStop(6.0, 127, 29, 29),
];

Color _sampleBands(List<_BandStop> ramp, double v) {
  if (v.isNaN) return const Color(0x00000000);
  if (v <= ramp.first.v) {
    return Color.fromARGB(255, ramp.first.r, ramp.first.g, ramp.first.b);
  }
  for (int i = 0; i < ramp.length - 1; i++) {
    final a = ramp[i];
    final b = ramp[i + 1];
    if (v >= a.v && v <= b.v) {
      final k = (v - a.v) / (b.v - a.v);
      return Color.fromARGB(
        255,
        (a.r + (b.r - a.r) * k).round(),
        (a.g + (b.g - a.g) * k).round(),
        (a.b + (b.b - a.b) * k).round(),
      );
    }
  }
  final last = ramp.last;
  return Color.fromARGB(255, last.r, last.g, last.b);
}

Color sstColor(double celsius) {
  if (celsius.isNaN) return const Color(0x00000000);
  final t = (celsius - 9.0) / (25.0 - 9.0);
  return kSstRamp.sample(t);
}

Color chlColor(double mg) {
  if (mg.isNaN) return const Color(0x00000000);
  final t = (math.log(mg) / math.ln10 - math.log(0.05) / math.ln10) /
      (math.log(20) / math.ln10 - math.log(0.05) / math.ln10);
  return kChlRamp.sample(t);
}

Color vizColor(double ft) => _sampleBands(_vizRamp, ft);
Color windColor(double kt) => _sampleBands(_windRamp, kt);
Color swellColor(double hsM) => _sampleBands(_swellRampM, hsM);
