import 'package:flutter_test/flutter_test.dart';
import 'package:shoudidive/colormap.dart';

void main() {
  group('SST colormap', () {
    test('NaN → fully transparent', () {
      final c = sstColor(double.nan);
      expect(c.alpha, 0);
    });

    test('9°C → cool blue (low end of ramp)', () {
      final c = sstColor(9.0);
      expect(c.red, lessThan(50));
      expect(c.blue, greaterThan(100));
    });

    test('25°C → warm red (high end of ramp)', () {
      final c = sstColor(25.0);
      expect(c.red, greaterThan(150));
      expect(c.blue, lessThan(80));
    });

    test('17°C (mid range) → teal-ish', () {
      final c = sstColor(17.0);
      // Mid-range stop is around (120,220,220)
      expect(c.green, greaterThan(160));
      expect(c.blue, greaterThan(160));
    });
  });

  group('Chlorophyll log colormap', () {
    test('NaN → transparent', () {
      expect(chlColor(double.nan).alpha, 0);
    });

    test('0.05 mg/m³ (lo end) → deep navy', () {
      final c = chlColor(0.05);
      expect(c.red, lessThan(50));
      expect(c.green, lessThan(80));
      expect(c.blue, greaterThan(100));
    });

    test('20 mg/m³ (hi end) → forest green', () {
      final c = chlColor(20.0);
      expect(c.green, greaterThan(100));
      expect(c.red, lessThan(80));
    });
  });

  group('Visibility colormap', () {
    test('5 ft → orange (Poor band)', () {
      final c = vizColor(5.0);
      expect(c.red, greaterThan(180));
      expect(c.green, greaterThan(30));
      expect(c.blue, lessThan(80));
    });

    test('60 ft → deep blue (Excellent band)', () {
      final c = vizColor(60.0);
      expect(c.blue, greaterThan(120));
      expect(c.red, lessThan(50));
    });
  });

  group('Wind colormap', () {
    test('0 kt → near white', () {
      final c = windColor(0);
      expect(c.red, greaterThan(200));
      expect(c.green, greaterThan(200));
      expect(c.blue, greaterThan(200));
    });

    test('30 kt → red-ish (top of ramp)', () {
      final c = windColor(30);
      expect(c.red, greaterThan(140));
    });
  });

  group('Swell colormap (Hs in metres)', () {
    test('0 m → glassy near-white', () {
      final c = swellColor(0);
      expect(c.red, greaterThan(220));
      expect(c.blue, greaterThan(240));
    });

    test('5 m → red (XL+)', () {
      final c = swellColor(5);
      expect(c.red, greaterThan(120));
      expect(c.green, lessThan(80));
    });
  });
}
