import 'dart:math' as math;
import 'dart:ui' show Rect, Offset, Size;

class BBox {
  final double lngMin;
  final double latMin;
  final double lngMax;
  final double latMax;
  const BBox(this.lngMin, this.latMin, this.lngMax, this.latMax);

  factory BBox.fromList(List<dynamic> v) =>
      BBox(v[0] as double, v[1] as double, v[2] as double, v[3] as double);

  static const ca = BBox(-124.0, 31.8, -116.8, 37.6);

  double get widthDeg => lngMax - lngMin;
  double get heightDeg => latMax - latMin;

  double get _midLatRad => ((latMin + latMax) / 2) * math.pi / 180;
  double get _cosMidLat => math.cos(_midLatRad);

  double get geoAspect =>
      ((lngMax - lngMin) * _cosMidLat) / (latMax - latMin);
}

class FittedRect {
  final double marginX;
  final double marginY;
  final double innerW;
  final double innerH;
  const FittedRect(this.marginX, this.marginY, this.innerW, this.innerH);

  Rect toRect() => Rect.fromLTWH(marginX, marginY, innerW, innerH);
}

FittedRect fitToBox(BBox bbox, Size container) {
  final w = container.width;
  final h = container.height;
  if (w <= 0 || h <= 0) return const FittedRect(0, 0, 0, 0);
  final containerAspect = w / h;
  final geoAspect = bbox.geoAspect;
  double innerW;
  double innerH;
  if (containerAspect > geoAspect) {
    innerH = h;
    innerW = h * geoAspect;
  } else {
    innerW = w;
    innerH = w / geoAspect;
  }
  return FittedRect((w - innerW) / 2, (h - innerH) / 2, innerW, innerH);
}

Offset project(BBox bbox, FittedRect rect, double lng, double lat) {
  final x = rect.marginX +
      ((lng - bbox.lngMin) / (bbox.lngMax - bbox.lngMin)) * rect.innerW;
  final y = rect.marginY +
      ((bbox.latMax - lat) / (bbox.latMax - bbox.latMin)) * rect.innerH;
  return Offset(x, y);
}

({double lng, double lat}) unproject(
    BBox bbox, FittedRect rect, double x, double y) {
  final lng = bbox.lngMin +
      ((x - rect.marginX) / rect.innerW) * (bbox.lngMax - bbox.lngMin);
  final lat = bbox.latMax -
      ((y - rect.marginY) / rect.innerH) * (bbox.latMax - bbox.latMin);
  return (lng: lng, lat: lat);
}
