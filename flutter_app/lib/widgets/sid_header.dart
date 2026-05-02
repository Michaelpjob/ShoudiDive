import 'dart:async';

import 'package:flutter/material.dart';

import '../theme/sid_tokens.dart';
import 'sid_logo.dart';
import 'sid_moon_icon.dart';

/// Top-of-screen chrome: logo + wordmark + region subtitle, plus a
/// Live indicator with auto-ticking UTC clock, a moon-phase icon,
/// and an info button.
///
/// `forecastTime` lets the moon icon reflect a future date (e.g. the
/// active wind/swell slot anchor) so the phase updates as the user
/// scrubs the time slider. Pass null to show the current phase.
class SidHeader extends StatefulWidget {
  final String regionSubtitle;
  final VoidCallback? onInfo;
  final DateTime? forecastTime;

  const SidHeader({
    super.key,
    this.regionSubtitle = '',
    this.onInfo,
    this.forecastTime,
  });

  @override
  State<SidHeader> createState() => _SidHeaderState();
}

class _SidHeaderState extends State<SidHeader> {
  late Timer _ticker;
  late String _liveTime;

  @override
  void initState() {
    super.initState();
    _liveTime = _formatUtc(DateTime.now().toUtc());
    // Tick every 30s — a minute display granularity is plenty.
    _ticker = Timer.periodic(const Duration(seconds: 30), (_) {
      if (!mounted) return;
      final next = _formatUtc(DateTime.now().toUtc());
      if (next != _liveTime) setState(() => _liveTime = next);
    });
  }

  @override
  void dispose() {
    _ticker.cancel();
    super.dispose();
  }

  String _formatUtc(DateTime t) {
    final hh = t.hour.toString().padLeft(2, '0');
    final mm = t.minute.toString().padLeft(2, '0');
    return '$hh:$mm UTC';
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(SidSpacing.pageH, 6, SidSpacing.pageH, 10),
      child: Row(
        children: [
          const SidLogo(size: 36),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  'ShouldIDive',
                  style: SidType.title.copyWith(height: 1.0),
                  overflow: TextOverflow.ellipsis,
                ),
                if (widget.regionSubtitle.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(
                    widget.regionSubtitle,
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w500,
                      color: SidColors.ink2,
                      letterSpacing: 0.1,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: 8),
          _LiveCluster(time: _liveTime),
          const SizedBox(width: 8),
          SidMoonIcon(when: widget.forecastTime, size: 22),
          const SizedBox(width: 6),
          _InfoButton(onPressed: widget.onInfo),
        ],
      ),
    );
  }
}

class _LiveCluster extends StatelessWidget {
  final String time;
  const _LiveCluster({required this.time});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 7,
          height: 7,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: SidColors.live,
            boxShadow: const [
              BoxShadow(
                color: Color(0x2E22C55E), // live @ 18%
                blurRadius: 0,
                spreadRadius: 2,
              ),
            ],
          ),
        ),
        const SizedBox(width: 6),
        const Text(
          'Live',
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            color: SidColors.ink,
          ),
        ),
        const SizedBox(width: 4),
        Text(
          time,
          style: const TextStyle(
            fontSize: 11,
            fontFamily: 'monospace',
            fontWeight: FontWeight.w500,
            color: SidColors.ink3,
          ),
        ),
      ],
    );
  }
}

class _InfoButton extends StatelessWidget {
  final VoidCallback? onPressed;
  const _InfoButton({this.onPressed});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: SidColors.card,
      shape: const CircleBorder(side: BorderSide(color: SidColors.hairline)),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onPressed,
        child: const SizedBox(
          width: 30,
          height: 30,
          child: Center(
            child: Text(
              'i',
              style: TextStyle(
                fontFamily: 'Georgia',
                fontStyle: FontStyle.italic,
                fontWeight: FontWeight.w700,
                fontSize: 14,
                color: SidColors.ink2,
              ),
            ),
          ),
        ),
      ),
    );
  }
}
