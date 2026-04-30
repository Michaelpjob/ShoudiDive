import 'package:flutter/material.dart';

import 'forecast.dart';

class TimeSlider extends StatelessWidget {
  final ForecastSummary summary;
  final int activeIndex;
  final ValueChanged<int> onChanged;
  final String unit;

  const TimeSlider({
    super.key,
    required this.summary,
    required this.activeIndex,
    required this.onChanged,
    required this.unit,
  });

  @override
  Widget build(BuildContext context) {
    final slots = summary.slots;
    if (slots.isEmpty) return const SizedBox.shrink();
    final clampedIdx = activeIndex.clamp(0, slots.length - 1);
    final active = slots[clampedIdx];

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _BucketBadge(
                title: '${active.dayLabel.isNotEmpty ? active.dayLabel : active.weekday}'
                    ' · ${active.bucket}',
                hours: '${active.bucketDef.startHourLocal}–'
                    '${active.bucketDef.endHourLocal}',
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  _formatStat(active),
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Colors.white70,
                      ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              showValueIndicator: ShowValueIndicator.always,
              trackHeight: 4,
              activeTickMarkColor: Colors.white,
              inactiveTickMarkColor: Colors.white24,
            ),
            child: Slider(
              min: 0,
              max: (slots.length - 1).toDouble(),
              divisions: slots.length - 1,
              value: clampedIdx.toDouble(),
              label: '${active.weekday} ${active.bucket}',
              onChanged: (v) => onChanged(v.round()),
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(left: 8, right: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  slots.first.dayLabel.isNotEmpty
                      ? slots.first.dayLabel
                      : 'Now',
                  style: const TextStyle(color: Colors.white54, fontSize: 11),
                ),
                Text(
                  '+${slots.last.day}d ${slots.last.bucket}',
                  style: const TextStyle(color: Colors.white54, fontSize: 11),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _formatStat(ForecastBucket b) {
    final mean = b.meanPrimary;
    if (mean == null) return '';
    final dir = b.meanDirDeg;
    final dirStr = dir == null ? '' : ' · ${_compass(dir)}';
    return 'mean ${mean.toStringAsFixed(1)} $unit$dirStr';
  }

  static const _cardinals = [
    'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
    'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW',
  ];

  static String _compass(double deg) {
    final idx = (deg / 22.5).round() % 16;
    return _cardinals[idx];
  }
}

class _BucketBadge extends StatelessWidget {
  final String title;
  final String hours;
  const _BucketBadge({required this.title, required this.hours});
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(title,
              style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.w500)),
          Text(hours,
              style: const TextStyle(
                  color: Colors.white60,
                  fontSize: 10,
                  fontFamily: 'monospace')),
        ],
      ),
    );
  }
}
