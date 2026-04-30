import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'bbox.dart';
import 'grid.dart';

class _Particle {
  double lng;
  double lat;
  double age;
  double maxAge;
  Offset prev;
  _Particle(this.lng, this.lat, this.age, this.maxAge, this.prev);
}

class WindParticlesOverlay extends StatefulWidget {
  final BBox bbox;
  final UVGrid uv;
  final int count;

  const WindParticlesOverlay({
    super.key,
    required this.bbox,
    required this.uv,
    this.count = 240,
  });

  @override
  State<WindParticlesOverlay> createState() => _WindParticlesOverlayState();
}

class _WindParticlesOverlayState extends State<WindParticlesOverlay>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  final math.Random _rng = math.Random(0);
  late List<_Particle> _particles;
  Duration _last = Duration.zero;
  Size _size = Size.zero;

  @override
  void initState() {
    super.initState();
    _particles = List.generate(widget.count, (_) => _spawn(true));
    _ctrl = AnimationController.unbounded(vsync: this)
      ..addListener(_onTick)
      ..animateTo(1e9, duration: const Duration(days: 365));
  }

  _Particle _spawn(bool initial) {
    final lng = widget.bbox.lngMin + _rng.nextDouble() * widget.bbox.widthDeg;
    final lat = widget.bbox.latMin + _rng.nextDouble() * widget.bbox.heightDeg;
    final maxAge = 1.5 + _rng.nextDouble() * 2.5;
    final age = initial ? _rng.nextDouble() * maxAge : 0.0;
    return _Particle(lng, lat, age, maxAge, Offset.zero);
  }

  void _onTick() {
    final now = _ctrl.lastElapsedDuration ?? Duration.zero;
    final dt = (now - _last).inMicroseconds / 1e6;
    _last = now;
    if (dt <= 0 || dt > 0.1) return;
    final uv = widget.uv;
    final fitted = fitToBox(widget.bbox, _size);
    for (final p in _particles) {
      // m/s components → degrees-per-second roughly. 1° lat ≈ 111 km.
      // dt scaled so motion is visible but not jittery (~6× real-time).
      final fx = ((p.lng - widget.bbox.lngMin) / widget.bbox.widthDeg) *
          (uv.width - 1);
      final fy = ((widget.bbox.latMax - p.lat) / widget.bbox.heightDeg) *
          (uv.height - 1);
      final u = _bilin(uv.u, uv.width, uv.height, fx, fy);
      final v = _bilin(uv.v, uv.width, uv.height, fx, fy);
      if (u.isNaN || v.isNaN) {
        p.age = p.maxAge + 1;
      } else {
        p.prev = project(widget.bbox, fitted, p.lng, p.lat);
        // Note: in the manifest, U is east-component, V is north-component.
        // Move lat by V (positive = north), lng by U (positive = east).
        const speedScale = 6.0; // visual factor
        p.lng += (u / 111000.0) * speedScale * dt;
        p.lat += (v / 111000.0) * speedScale * dt;
        p.age += dt;
      }
      if (p.age > p.maxAge ||
          p.lng < widget.bbox.lngMin ||
          p.lng > widget.bbox.lngMax ||
          p.lat < widget.bbox.latMin ||
          p.lat > widget.bbox.latMax) {
        final fresh = _spawn(false);
        p.lng = fresh.lng;
        p.lat = fresh.lat;
        p.age = 0;
        p.maxAge = fresh.maxAge;
        p.prev = Offset.zero;
      }
    }
    if (mounted) setState(() {});
  }

  static double _bilin(
      List<double> arr, int w, int h, double fx, double fy) {
    if (fx < 0 || fx > w - 1 || fy < 0 || fy > h - 1) return double.nan;
    final x0 = fx.floor();
    final x1 = math.min(x0 + 1, w - 1);
    final y0 = fy.floor();
    final y1 = math.min(y0 + 1, h - 1);
    final tx = fx - x0;
    final ty = fy - y0;
    final v00 = arr[y0 * w + x0];
    final v10 = arr[y0 * w + x1];
    final v01 = arr[y1 * w + x0];
    final v11 = arr[y1 * w + x1];
    if (v00.isNaN || v10.isNaN || v01.isNaN || v11.isNaN) return double.nan;
    return v00 * (1 - tx) * (1 - ty) +
        v10 * tx * (1 - ty) +
        v01 * (1 - tx) * ty +
        v11 * tx * ty;
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        _size = Size(constraints.maxWidth, constraints.maxHeight);
        return CustomPaint(
          size: _size,
          painter: _ParticlePainter(
            bbox: widget.bbox,
            particles: _particles,
            size: _size,
          ),
        );
      },
    );
  }
}

class _ParticlePainter extends CustomPainter {
  final BBox bbox;
  final List<_Particle> particles;
  final Size size;
  _ParticlePainter({
    required this.bbox,
    required this.particles,
    required this.size,
  });

  @override
  void paint(Canvas canvas, Size sz) {
    final fitted = fitToBox(bbox, sz);
    final headPaint = Paint()..color = const Color(0xFFFFFFFF);
    final tailPaint = Paint()
      ..color = const Color(0x66FFFFFF)
      ..strokeWidth = 1.0
      ..strokeCap = StrokeCap.round;
    for (final p in particles) {
      final pos = project(bbox, fitted, p.lng, p.lat);
      if (p.prev != Offset.zero) {
        canvas.drawLine(p.prev, pos, tailPaint);
      }
      canvas.drawCircle(pos, 1.4, headPaint);
    }
  }

  @override
  bool shouldRepaint(covariant _ParticlePainter oldDelegate) => true;
}
