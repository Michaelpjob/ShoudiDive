import 'package:flutter/material.dart';

import '../theme/sid_tokens.dart';

/// `OVERLAYS  [MPAs]  [Banks]                NOAA HRRR · 3 km` strip.
class SidOverlayRow extends StatelessWidget {
  final String? source;
  final bool mpaOn;
  final bool banksOn;
  final ValueChanged<bool> onMpaToggle;
  final ValueChanged<bool>? onBanksToggle;

  const SidOverlayRow({
    super.key,
    required this.source,
    required this.mpaOn,
    required this.banksOn,
    required this.onMpaToggle,
    this.onBanksToggle,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(SidSpacing.pageH, 10, SidSpacing.pageH, 10),
      child: Row(
        children: [
          const Text('OVERLAYS', style: SidType.eyebrow),
          const SizedBox(width: 8),
          _Pill(
            label: 'MPAs',
            on: mpaOn,
            onTap: () => onMpaToggle(!mpaOn),
          ),
          const SizedBox(width: 8),
          _Pill(
            label: 'Banks',
            on: banksOn,
            onTap: onBanksToggle == null ? null : () => onBanksToggle!(!banksOn),
          ),
          const Spacer(),
          if (source != null && source!.isNotEmpty)
            Flexible(
              child: Text(
                source!,
                style: const TextStyle(
                  fontSize: 10,
                  fontFamily: 'monospace',
                  fontWeight: FontWeight.w500,
                  color: SidColors.ink3,
                  letterSpacing: -0.1,
                ),
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.right,
              ),
            ),
        ],
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  final String label;
  final bool on;
  final VoidCallback? onTap;
  const _Pill({required this.label, required this.on, this.onTap});

  @override
  Widget build(BuildContext context) {
    final disabled = onTap == null;
    return Material(
      color: on ? SidColors.ink : Colors.transparent,
      shape: StadiumBorder(
        side: BorderSide(
          color: on
              ? SidColors.ink
              : (disabled ? SidColors.hairline : SidColors.hairline),
        ),
      ),
      child: InkWell(
        customBorder: const StadiumBorder(),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 4.5),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
              letterSpacing: -0.1,
              color: on
                  ? SidColors.card
                  : (disabled ? SidColors.ink3 : SidColors.ink2),
            ),
          ),
        ),
      ),
    );
  }
}
