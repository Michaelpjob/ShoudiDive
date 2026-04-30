import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import 'bbox.dart';
import 'colormap.dart';
import 'geojson_loader.dart';
import 'grid.dart';
import 'static_data.dart';

typedef ColorMapper = Color Function(double v);

class MapLayerSpec {
  final Grid grid;
  final ColorMapper colorMapper;
  const MapLayerSpec({required this.grid, required this.colorMapper});
}

class MapPin {
  final double lng;
  final double lat;
  const MapPin(this.lng, this.lat);
}

class MapView extends StatefulWidget {
  final BBox bbox;
  final MapLayerSpec? layer;
  final GeoFeatureCollection? land;
  final GeoFeatureCollection? mpa;
  final double opacity;
  final MapPin? pin;
  final void Function(double lng, double lat)? onTapPoint;
  final bool showLabels;
  final bool showSpots;

  const MapView({
    super.key,
    required this.bbox,
    required this.layer,
    required this.land,
    required this.mpa,
    this.opacity = 0.85,
    this.pin,
    this.onTapPoint,
    this.showLabels = true,
    this.showSpots = true,
  });

  @override
  State<MapView> createState() => _MapViewState();
}

class _MapViewState extends State<MapView> {
  ui.Image? _layerImage;
  Object? _builtFromLayer;

  @override
  void initState() {
    super.initState();
    _rebuildLayerImage();
  }

  @override
  void didUpdateWidget(covariant MapView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.layer, widget.layer)) {
      _rebuildLayerImage();
    }
  }

  void _rebuildLayerImage() {
    final layer = widget.layer;
    _builtFromLayer = layer;
    if (layer == null) {
      setState(() => _layerImage = null);
      return;
    }
    final grid = layer.grid;
    final pixels = Uint8List(grid.width * grid.height * 4);
    for (int i = 0; i < grid.data.length; i++) {
      final v = grid.data[i];
      if (v.isNaN) {
        pixels[i * 4 + 3] = 0;
        continue;
      }
      final c = layer.colorMapper(v);
      final argb = c.toARGB32();
      pixels[i * 4] = (argb >> 16) & 0xFF;
      pixels[i * 4 + 1] = (argb >> 8) & 0xFF;
      pixels[i * 4 + 2] = argb & 0xFF;
      pixels[i * 4 + 3] = (argb >> 24) & 0xFF;
    }
    ui.decodeImageFromPixels(
      pixels,
      grid.width,
      grid.height,
      ui.PixelFormat.rgba8888,
      (image) {
        if (!mounted) return;
        if (!identical(_builtFromLayer, layer)) {
          image.dispose();
          return;
        }
        setState(() {
          _layerImage?.dispose();
          _layerImage = image;
        });
      },
    );
  }

  @override
  void dispose() {
    _layerImage?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(constraints.maxWidth, constraints.maxHeight);
        return InteractiveViewer(
          minScale: 1.0,
          maxScale: 8.0,
          panEnabled: true,
          scaleEnabled: true,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: widget.onTapPoint == null
                ? null
                : (details) {
                    final fitted = fitToBox(widget.bbox, size);
                    final p = details.localPosition;
                    if (p.dx < fitted.marginX ||
                        p.dx > fitted.marginX + fitted.innerW ||
                        p.dy < fitted.marginY ||
                        p.dy > fitted.marginY + fitted.innerH) {
                      return;
                    }
                    final ll = unproject(widget.bbox, fitted, p.dx, p.dy);
                    widget.onTapPoint!(ll.lng, ll.lat);
                  },
            child: CustomPaint(
              size: size,
              painter: _MapPainter(
                bbox: widget.bbox,
                layerImage: _layerImage,
                land: widget.land,
                mpa: widget.mpa,
                opacity: widget.opacity,
                pin: widget.pin,
                showLabels: widget.showLabels,
                showSpots: widget.showSpots,
              ),
            ),
          ),
        );
      },
    );
  }
}

class _MapPainter extends CustomPainter {
  final BBox bbox;
  final ui.Image? layerImage;
  final GeoFeatureCollection? land;
  final GeoFeatureCollection? mpa;
  final double opacity;
  final MapPin? pin;
  final bool showLabels;
  final bool showSpots;

  _MapPainter({
    required this.bbox,
    required this.layerImage,
    required this.land,
    required this.mpa,
    required this.opacity,
    required this.pin,
    required this.showLabels,
    required this.showSpots,
  });

  static const _seaDeeper = Color(0xFF071017);
  static const _seaDeep = Color(0xFF0B1B26);
  static const _sea = Color(0xFF12313F);
  static const _land = Color(0xFF1F2A37);
  static const _landEdge = Color(0xFF263241);
  static const _grid = Color(0x1FFFFFFF);
  static const _mpaFill = Color(0x1AFFB347);
  static const _mpaEdge = Color(0x66FFB347);

  @override
  void paint(Canvas canvas, Size size) {
    final fitted = fitToBox(bbox, size);

    // Background sea fill (full canvas, not just inner rect)
    canvas.drawRect(
      Rect.fromLTWH(0, 0, size.width, size.height),
      Paint()..color = _seaDeeper,
    );

    // Inner-rect ocean gradient
    final innerRect = fitted.toRect();
    canvas.drawRect(
      innerRect,
      Paint()
        ..shader = const LinearGradient(
          colors: [_seaDeeper, _seaDeep, _sea],
          stops: [0.0, 0.6, 1.0],
        ).createShader(innerRect),
    );

    // Graticule
    final gridPaint = Paint()
      ..color = _grid
      ..strokeWidth = 0.6
      ..style = PaintingStyle.stroke;
    for (var lat = bbox.latMin.ceil(); lat <= bbox.latMax.floor(); lat++) {
      final y = project(bbox, fitted, bbox.lngMin, lat.toDouble()).dy;
      _drawDashedLine(canvas, Offset(0, y), Offset(size.width, y), gridPaint);
    }
    for (var lng = bbox.lngMin.ceil(); lng <= bbox.lngMax.floor(); lng++) {
      final x = project(bbox, fitted, lng.toDouble(), bbox.latMin).dx;
      _drawDashedLine(canvas, Offset(x, 0), Offset(x, size.height), gridPaint);
    }

    // Data overlay
    if (layerImage != null) {
      final src = Rect.fromLTWH(
        0,
        0,
        layerImage!.width.toDouble(),
        layerImage!.height.toDouble(),
      );
      final paint = Paint()
        ..color = Color.fromRGBO(0, 0, 0, opacity)
        ..filterQuality = FilterQuality.medium;
      canvas.saveLayer(innerRect, paint);
      canvas.drawImageRect(layerImage!, src, innerRect, Paint()..filterQuality = FilterQuality.medium);
      canvas.restore();
    }

    // MPA polygons
    if (mpa != null) {
      final fillPaint = Paint()..color = _mpaFill;
      final edgePaint = Paint()
        ..color = _mpaEdge
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.0;
      canvas.save();
      canvas.clipRect(innerRect);
      for (final poly in mpa!.polygons) {
        final path = _polygonToPath(poly, fitted);
        canvas.drawPath(path, fillPaint);
        canvas.drawPath(path, edgePaint);
      }
      canvas.restore();
    }

    // Land
    if (land != null) {
      final fillPaint = Paint()..color = _land;
      final edgePaint = Paint()
        ..color = _landEdge
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.0;
      for (final poly in land!.polygons) {
        final path = _polygonToPath(poly, fitted);
        canvas.drawPath(path, fillPaint);
        canvas.drawPath(path, edgePaint);
      }
    }

    // Saved spots
    if (showSpots) {
      final ringPaint = Paint()
        ..color = const Color(0xCCFFFFFF)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5;
      final fillPaint = Paint()..color = const Color(0xFF22C55E);
      for (final s in kSavedSpots) {
        final p = project(bbox, fitted, s.lng, s.lat);
        canvas.drawCircle(p, 4, fillPaint);
        canvas.drawCircle(p, 4, ringPaint);
      }
    }

    // Place labels
    if (showLabels) {
      for (final lbl in kPlaceLabels) {
        final p = project(bbox, fitted, lbl.lng, lbl.lat);
        final tp = TextPainter(
          text: TextSpan(
            text: lbl.text,
            style: TextStyle(
              color: lbl.muted
                  ? const Color(0xCCB0BCC8)
                  : const Color(0xFFE7EEF4),
              fontSize: lbl.fontSize,
              fontWeight: FontWeight.w500,
              fontStyle: lbl.italic ? FontStyle.italic : FontStyle.normal,
              letterSpacing: 0.4,
              shadows: const [
                Shadow(color: Color(0xFF000000), offset: Offset(0, 1), blurRadius: 2),
              ],
            ),
          ),
          textDirection: TextDirection.ltr,
        );
        tp.layout();
        tp.paint(
          canvas,
          Offset(p.dx - tp.width / 2, p.dy - tp.height / 2),
        );
      }
    }

    // Pin marker
    if (pin != null) {
      final p = project(bbox, fitted, pin!.lng, pin!.lat);
      final ringPaint = Paint()
        ..color = const Color(0xFFFFFFFF)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.0;
      final fillPaint = Paint()..color = const Color(0xFFFF4D4F);
      canvas.drawCircle(p, 7, ringPaint);
      canvas.drawCircle(p, 5, fillPaint);
      canvas.drawLine(p.translate(-12, 0), p.translate(-3, 0), ringPaint);
      canvas.drawLine(p.translate(3, 0), p.translate(12, 0), ringPaint);
      canvas.drawLine(p.translate(0, -12), p.translate(0, -3), ringPaint);
      canvas.drawLine(p.translate(0, 3), p.translate(0, 12), ringPaint);
    }
  }

  Path _polygonToPath(Polygon poly, FittedRect fitted) {
    final path = Path()..fillType = PathFillType.evenOdd;
    for (final ring in poly.rings) {
      if (ring.isEmpty) continue;
      final p0 = project(bbox, fitted, ring[0][0], ring[0][1]);
      path.moveTo(p0.dx, p0.dy);
      for (var i = 1; i < ring.length; i++) {
        final p = project(bbox, fitted, ring[i][0], ring[i][1]);
        path.lineTo(p.dx, p.dy);
      }
      path.close();
    }
    return path;
  }

  void _drawDashedLine(Canvas canvas, Offset a, Offset b, Paint paint) {
    const dashOn = 2.0;
    const dashOff = 4.0;
    final dx = b.dx - a.dx;
    final dy = b.dy - a.dy;
    final dist = (dx * dx + dy * dy);
    if (dist == 0) return;
    final length = a == b ? 0.0 : (dx == 0 ? dy.abs() : dx.abs());
    final ux = dx / length;
    final uy = dy / length;
    double consumed = 0;
    while (consumed < length) {
      final start = consumed;
      final end = (consumed + dashOn).clamp(0, length);
      canvas.drawLine(
        Offset(a.dx + ux * start, a.dy + uy * start),
        Offset(a.dx + ux * end, a.dy + uy * end),
        paint,
      );
      consumed += dashOn + dashOff;
    }
  }

  @override
  bool shouldRepaint(covariant _MapPainter old) =>
      old.layerImage != layerImage ||
      old.land != land ||
      old.mpa != mpa ||
      old.opacity != opacity ||
      old.pin?.lng != pin?.lng ||
      old.pin?.lat != pin?.lat ||
      old.showLabels != showLabels ||
      old.showSpots != showSpots;
}

ColorMapper colorMapperForLayerId(String id) {
  switch (id) {
    case 'sst':
      return sstColor;
    case 'chl':
      return chlColor;
    case 'viz':
      return vizColor;
    case 'wind':
      return windColor;
    case 'swell':
    case 'wave':
      return swellColor;
    default:
      return (v) => const Color(0xFFCCCCCC);
  }
}
