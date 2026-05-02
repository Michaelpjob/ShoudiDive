import 'package:flutter/material.dart';

import '../theme/sid_tokens.dart';

/// Red rhombus with a downward triangle inside, on a rounded white card.
/// Mirrors the prototype's `<SIDLogo size={size}/>` SVG.
class SidLogo extends StatelessWidget {
  final double size;
  const SidLogo({super.key, this.size = 36});

  @override
  Widget build(BuildContext context) {
    return SizedBox.square(
      dimension: size,
      child: CustomPaint(painter: _SidLogoPainter()),
    );
  }
}

class _SidLogoPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    // SVG is authored at 36x36 — scale uniformly to the requested size.
    final s = size.width / 36.0;
    Offset p(double x, double y) => Offset(x * s, y * s);

    // Rounded card background (32x32, 8px radius, inset by 2px)
    final card = RRect.fromRectAndRadius(
      Rect.fromLTWH(2 * s, 2 * s, 32 * s, 32 * s),
      Radius.circular(8 * s),
    );
    canvas.drawRRect(card, Paint()..color = SidColors.card);
    canvas.drawRRect(
      card,
      Paint()
        ..color = const Color(0x0F0F172A) // ink @ ~6%
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.0,
    );

    // Red rhombus: (18,8) → (26,16) → (18,24) → (10,16)
    final rhombus = Path()
      ..moveTo(p(18, 8).dx, p(18, 8).dy)
      ..lineTo(p(26, 16).dx, p(26, 16).dy)
      ..lineTo(p(18, 24).dx, p(18, 24).dy)
      ..lineTo(p(10, 16).dx, p(10, 16).dy)
      ..close();
    canvas.drawPath(rhombus, Paint()..color = SidColors.logoRed);

    // Inner downward triangle (negative space): (14,14) → (22,14) → (18,22)
    final tri = Path()
      ..moveTo(p(14, 14).dx, p(14, 14).dy)
      ..lineTo(p(22, 14).dx, p(22, 14).dy)
      ..lineTo(p(18, 22).dx, p(18, 22).dy)
      ..close();
    canvas.drawPath(tri, Paint()..color = SidColors.card);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
