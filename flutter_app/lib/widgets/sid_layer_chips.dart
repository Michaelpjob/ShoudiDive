import 'package:flutter/material.dart';

import '../theme/sid_tokens.dart';

/// Five-layer chip row from the v2 mockups. Renders one flexible chip
/// per layer in `layerIds`, in the canonical SidLayers order
/// (Temp · Chl · Wind · Swell · Vis). Layers absent from `layerIds`
/// are simply omitted — useful when the manifest hasn't shipped a
/// product yet.
class SidLayerChips extends StatelessWidget {
  final String activeId;
  final ValueChanged<String> onChange;
  final Set<String> layerIds;

  const SidLayerChips({
    super.key,
    required this.activeId,
    required this.onChange,
    required this.layerIds,
  });

  @override
  Widget build(BuildContext context) {
    final visible = SidLayers.all.where((l) => layerIds.contains(l.id)).toList();
    if (visible.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: SidSpacing.mapPadH),
      child: Row(
        children: [
          for (var i = 0; i < visible.length; i++) ...[
            Expanded(
              child: _Chip(
                spec: visible[i],
                selected: visible[i].id == activeId,
                onTap: () => onChange(visible[i].id),
              ),
            ),
            if (i < visible.length - 1) const SizedBox(width: 6),
          ],
        ],
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final SidLayerSpec spec;
  final bool selected;
  final VoidCallback onTap;

  const _Chip({
    required this.spec,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final fg = selected ? SidColors.card : SidColors.ink;
    final unitColor = selected
        ? SidColors.card.withValues(alpha: 0.65)
        : SidColors.ink3;

    return Material(
      color: selected ? SidColors.ink : SidColors.card,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(9),
        side: BorderSide(
          color: selected ? SidColors.ink : SidColors.hairline,
        ),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(9),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(0, 8, 0, 7),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                spec.label,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.1,
                  height: 1.1,
                  color: fg,
                ),
              ),
              const SizedBox(height: 1),
              Text(
                spec.unit,
                style: TextStyle(
                  fontSize: 9.5,
                  fontWeight: FontWeight.w500,
                  letterSpacing: 0.2,
                  color: unitColor,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
