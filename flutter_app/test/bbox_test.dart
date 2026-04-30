import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:shoudidive/bbox.dart';

void main() {
  group('BBox', () {
    test('CA bbox dimensions', () {
      const b = BBox.ca;
      expect(b.widthDeg, closeTo(7.2, 1e-9));
      expect(b.heightDeg, closeTo(5.8, 1e-9));
      expect(b.geoAspect, closeTo(1.03, 0.05));
    });
  });

  group('fitToBox', () {
    test('container wider than geo → pillarbox', () {
      final fit = fitToBox(BBox.ca, const Size(1000, 500));
      expect(fit.innerH, 500);
      expect(fit.innerW, closeTo(500 * BBox.ca.geoAspect, 1e-9));
      expect(fit.marginX, greaterThan(0));
      expect(fit.marginY, 0);
    });

    test('container taller than geo → letterbox', () {
      final fit = fitToBox(BBox.ca, const Size(400, 800));
      expect(fit.innerW, 400);
      expect(fit.innerH, closeTo(400 / BBox.ca.geoAspect, 1e-9));
      expect(fit.marginX, 0);
      expect(fit.marginY, greaterThan(0));
    });

    test('zero size returns zeroed rect', () {
      final fit = fitToBox(BBox.ca, Size.zero);
      expect(fit.innerW, 0);
      expect(fit.innerH, 0);
    });
  });

  group('project / unproject', () {
    test('roundtrip through La Jolla recovers input', () {
      const b = BBox.ca;
      final fit = fitToBox(b, const Size(800, 800));
      const lng = -117.27;
      const lat = 32.85;
      final p = project(b, fit, lng, lat);
      final ll = unproject(b, fit, p.dx, p.dy);
      expect(ll.lng, closeTo(lng, 1e-9));
      expect(ll.lat, closeTo(lat, 1e-9));
    });

    test('NW corner projects to inner top-left', () {
      const b = BBox.ca;
      final fit = fitToBox(b, const Size(800, 600));
      final p = project(b, fit, b.lngMin, b.latMax);
      expect(p.dx, closeTo(fit.marginX, 1e-9));
      expect(p.dy, closeTo(fit.marginY, 1e-9));
    });

    test('SE corner projects to inner bottom-right', () {
      const b = BBox.ca;
      final fit = fitToBox(b, const Size(800, 600));
      final p = project(b, fit, b.lngMax, b.latMin);
      expect(p.dx, closeTo(fit.marginX + fit.innerW, 1e-9));
      expect(p.dy, closeTo(fit.marginY + fit.innerH, 1e-9));
    });
  });
}
