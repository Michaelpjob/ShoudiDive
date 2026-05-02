import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../moon_phase.dart';
import '../theme/sid_tokens.dart';

/// Small disc rendering the moon phase at a given moment.
///
/// Standard rendering: a dark disc, then a lit semicircle on the
/// waxing/waning side, then an elliptical terminator that either
/// subtracts (crescent) or adds (gibbous) lit area.
///
/// `when` defaults to the current UTC instant. Pass a forecast time
/// (e.g. the active wind/swell slot's anchor) and the icon updates
/// to match that future date as the user scrubs the time slider.
class SidMoonIcon extends StatelessWidget {
  final DateTime? when;
  final double size;

  const SidMoonIcon({
    super.key,
    this.when,
    this.size = 22,
  });

  @override
  Widget build(BuildContext context) {
    final t = when ?? DateTime.now();
    final phase = moonPhase(t);
    return Tooltip(
      message: moonPhaseName(phase),
      child: SizedBox.square(
        dimension: size,
        child: CustomPaint(painter: _MoonPainter(phase: phase)),
      ),
    );
  }
}

class _MoonPainter extends CustomPainter {
  final double phase;
  _MoonPainter({required this.phase});

  @override
  void paint(Canvas canvas, Size size) {
    final r = size.width / 2;
    final center = Offset(size.width / 2, size.height / 2);

    final dark = Paint()..color = SidColors.ink;
    final lit = Paint()..color = SidColors.card;

    // Hairline ring so the dark disc reads against the cream page wash.
    final ring = Paint()
      ..color = SidColors.ink2.withValues(alpha: 0.3)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.8;

    // 1) Full dark disc.
    canvas.drawCircle(center, r, dark);

    // 2) New-moon shortcut.
    if (phase < 0.005 || phase > 0.995) {
      canvas.drawCircle(center, r, ring);
      return;
    }

    final phaseAngle = phase * 2 * math.pi;
    final cosPhase = math.cos(phaseAngle);
    final isWaxing = phase < 0.5;

    // 3) Lit semicircle on the appropriate side.
    final litRect = isWaxing
        ? Rect.fromLTWH(center.dx, center.dy - r, r, 2 * r)
        : Rect.fromLTWH(center.dx - r, center.dy - r, r, 2 * r);
    canvas.save();
    canvas.clipRect(litRect);
    canvas.drawCircle(center, r, lit);
    canvas.restore();

    // 4) Elliptical terminator.
    final ellipseW = cosPhase.abs() * 2 * r;
    final ellipseRect = Rect.fromCenter(
      center: center,
      width: ellipseW,
      height: 2 * r,
    );

    if (phase < 0.25 || phase > 0.75) {
      // Crescent: dark ellipse subtracts from the lit half.
      canvas.drawOval(ellipseRect, dark);
    } else {
      // Gibbous: lit ellipse adds onto the lit half.
      canvas.drawOval(ellipseRect, lit);
    }

    // 5) Hairline ring on top so the silhouette reads on a light bg.
    canvas.drawCircle(center, r, ring);
  }

  @override
  bool shouldRepaint(covariant _MoonPainter old) => old.phase != phase;
}
