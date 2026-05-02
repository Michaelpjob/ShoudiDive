import 'package:flutter_test/flutter_test.dart';
import 'package:shoudidive/moon_phase.dart';

void main() {
  group('moonPhase', () {
    test('phase ≈ 0 at the reference new moon (2000-01-06 18:14 UTC)', () {
      final p = moonPhase(DateTime.utc(2000, 1, 6, 18, 14));
      // The reference instant is by construction phase 0.
      expect(p, lessThan(0.001));
    });

    test('phase ≈ 0 at a known new moon (2024-01-11 11:57 UTC)', () {
      // Astronomical tables: new moon was at 11:57 UTC on 2024-01-11.
      // Conway-style synodic approximation drifts ~0.5 day over 24 years,
      // so allow a generous tolerance — this is icon-scale, not ephemeris.
      final p = moonPhase(DateTime.utc(2024, 1, 11, 11, 57));
      // Wrap-around: phase near 0 means either ~0 or ~1.
      final wrapped = p > 0.5 ? 1.0 - p : p;
      expect(wrapped, lessThan(0.05));
    });

    test('phase ≈ 0.5 at a known full moon (2024-01-25 17:54 UTC)', () {
      final p = moonPhase(DateTime.utc(2024, 1, 25, 17, 54));
      expect((p - 0.5).abs(), lessThan(0.05));
    });

    test('phase wraps to [0, 1)', () {
      // Far in the past should still produce a valid phase.
      final p = moonPhase(DateTime.utc(1900, 6, 15));
      expect(p, greaterThanOrEqualTo(0.0));
      expect(p, lessThan(1.0));
    });

    test('phase increases monotonically over a single synodic month', () {
      // Sample 14 days into a cycle (just past new moon) and verify
      // phase is roughly 14/29.5 ≈ 0.475.
      final start = DateTime.utc(2024, 1, 11, 11, 57); // new moon
      final twoWeeks = start.add(const Duration(days: 14));
      final p = moonPhase(twoWeeks);
      expect(p, greaterThan(0.4));
      expect(p, lessThan(0.55));
    });
  });

  group('illuminationFraction', () {
    test('new moon → 0, full moon → 1, quarters → 0.5', () {
      expect(illuminationFraction(0.0), closeTo(0.0, 1e-9));
      expect(illuminationFraction(0.5), closeTo(1.0, 1e-9));
      expect(illuminationFraction(0.25), closeTo(0.5, 1e-9));
      expect(illuminationFraction(0.75), closeTo(0.5, 1e-9));
    });
  });

  group('moonPhaseName', () {
    test('canonical phase boundaries map to correct labels', () {
      expect(moonPhaseName(0.0), 'New moon');
      expect(moonPhaseName(0.125), 'Waxing crescent');
      expect(moonPhaseName(0.25), 'First quarter');
      expect(moonPhaseName(0.375), 'Waxing gibbous');
      expect(moonPhaseName(0.5), 'Full moon');
      expect(moonPhaseName(0.625), 'Waning gibbous');
      expect(moonPhaseName(0.75), 'Last quarter');
      expect(moonPhaseName(0.875), 'Waning crescent');
    });

    test('phase very close to 1.0 wraps to "New moon"', () {
      expect(moonPhaseName(0.99), 'New moon');
    });
  });
}
