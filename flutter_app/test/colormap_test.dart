import 'dart:ui' show Color;

import 'package:flutter_test/flutter_test.dart';
import 'package:shoudidive/colormap.dart';

// Helpers — Color now exposes channels as 0..1 doubles. Keep tests in 0..255.
extension _Channels on Color {
  int get r255 => (r * 255).round();
  int get g255 => (g * 255).round();
  int get b255 => (b * 255).round();
  int get a255 => (a * 255).round();
}

void main() {
  group('SST colormap (palette anchored in °F)', () {
    test('NaN → fully transparent', () {
      expect(sstColor(double.nan).a255, 0);
    });

    test('9°C (~48°F) clamps to first stop — muted blue', () {
      // First stop is #5B8DB5 = (91, 141, 181)
      final c = sstColor(9.0);
      expect(c.b255, greaterThan(c.r255));
      expect(c.b255, greaterThan(150));
    });

    test('25°C (~77°F) clamps to last stop — cream yellow', () {
      // Last stop is #EAD68A = (234, 214, 138)
      final c = sstColor(25.0);
      expect(c.r255, greaterThan(200));
      expect(c.g255, greaterThan(180));
    });

    test('15.5°C (~60°F) — sage green mid', () {
      // 60°F stop is #9BCBB1 = (155, 203, 177)
      final c = sstColor(15.5);
      expect(c.g255, greaterThan(180));
    });
  });

  group('Chlorophyll colormap (mg/m³)', () {
    test('NaN → transparent', () {
      expect(chlColor(double.nan).a255, 0);
    });

    test('0.05 (below first stop) → deep navy "Gin"', () {
      // First stop #1F3A55 = (31, 58, 85)
      final c = chlColor(0.05);
      expect(c.r255, lessThan(60));
      expect(c.b255, greaterThan(c.r255));
    });

    test('20 (above last stop) → warm brown "Murky"', () {
      // Last stop #7A5A3C = (122, 90, 60)
      final c = chlColor(20.0);
      expect(c.r255, greaterThan(c.b255));
    });
  });

  group('Visibility colormap (ft)', () {
    test('5 ft — gray-blue "Poor"', () {
      // First stop #A8B8C8 = (168, 184, 200)
      final c = vizColor(5.0);
      expect(c.b255, greaterThan(c.r255));
      expect(c.r255, greaterThan(120));
    });

    test('60 ft (above) clamps to deep blue "Excellent"', () {
      // Last stop #1F4D75 = (31, 77, 117)
      final c = vizColor(60.0);
      expect(c.b255, greaterThan(c.r255));
      expect(c.r255, lessThan(60));
    });
  });

  group('Wind colormap (kt)', () {
    test('0 kt → pale blue "calm"', () {
      // First stop #CFDDED = (207, 221, 237)
      final c = windColor(0);
      expect(c.r255, greaterThan(190));
      expect(c.g255, greaterThan(190));
      expect(c.b255, greaterThan(220));
    });

    test('30 kt → warm peach (between 20 kt and gale)', () {
      final c = windColor(30);
      expect(c.r255, greaterThan(c.b255));
    });
  });

  group('Swell colormap (Hs in metres → palette in ft)', () {
    test('0 m → glassy near-white blue', () {
      // First stop #D1E8F5 = (209, 232, 245)
      final c = swellColor(0);
      expect(c.b255, greaterThan(220));
      expect(c.g255, greaterThan(200));
    });

    test('5 m (~16.4 ft) clamps to storm red', () {
      // Last stop #8B3A3A = (139, 58, 58)
      final c = swellColor(5);
      expect(c.r255, greaterThan(c.g255));
      expect(c.r255, greaterThan(100));
    });
  });
}
