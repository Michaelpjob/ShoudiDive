import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:shoudidive/bbox.dart';
import 'package:shoudidive/grid.dart';

Grid _grid3x3FromList(List<double> v) {
  final f = Float32List.fromList(v);
  return Grid(3, 3, f);
}

void main() {
  group('Grid bilinear', () {
    test('exact corner returns corner value', () {
      final g = _grid3x3FromList(const [
        1, 2, 3,
        4, 5, 6,
        7, 8, 9,
      ]);
      expect(g.sampleBilinear(0, 0), 1);
      expect(g.sampleBilinear(2, 0), 3);
      expect(g.sampleBilinear(0, 2), 7);
      expect(g.sampleBilinear(2, 2), 9);
    });

    test('halfway between known points averages them', () {
      final g = _grid3x3FromList(const [
        0, 10, 20,
        30, 40, 50,
        60, 70, 80,
      ]);
      // halfway between 0 and 10 → 5
      expect(g.sampleBilinear(0.5, 0), closeTo(5, 1e-9));
      // halfway between 0 and 30 → 15
      expect(g.sampleBilinear(0, 0.5), closeTo(15, 1e-9));
      // center of 0/10/30/40 → 20
      expect(g.sampleBilinear(0.5, 0.5), closeTo(20, 1e-9));
    });

    test('off-grid returns NaN', () {
      final g = _grid3x3FromList(const [1, 2, 3, 4, 5, 6, 7, 8, 9]);
      expect(g.sampleBilinear(-0.5, 0).isNaN, isTrue);
      expect(g.sampleBilinear(2.5, 0).isNaN, isTrue);
      expect(g.sampleBilinear(0, -0.5).isNaN, isTrue);
      expect(g.sampleBilinear(0, 2.5).isNaN, isTrue);
    });

    test('NaN-safe bilinear averages valid corners only', () {
      final g = _grid3x3FromList([
        double.nan, 10.0, 20.0,
        30.0, 40.0, 50.0,
        60.0, 70.0, 80.0,
      ]);
      // (0.5, 0.5) corners: NaN, 10, 30, 40 → mean of finite = (10+30+40)/3 ≈ 26.6
      expect(g.sampleBilinear(0.5, 0.5), closeTo((10 + 30 + 40) / 3, 1e-9));
    });
  });

  group('Grid sampleAt (lng/lat)', () {
    test('NW corner sample', () {
      // 3×3 grid with row-major; row 0 is top of bbox (lat = latMax).
      final g = _grid3x3FromList(const [
        1, 2, 3,
        4, 5, 6,
        7, 8, 9,
      ]);
      const b = BBox.ca;
      // lngMin / latMax → grid (0, 0) → 1
      expect(g.sampleAt(b, b.lngMin, b.latMax), closeTo(1, 1e-9));
      // lngMax / latMin → grid (2, 2) → 9
      expect(g.sampleAt(b, b.lngMax, b.latMin), closeTo(9, 1e-9));
    });
  });
}
