import 'package:flutter/material.dart';

/// Locked design tokens from the v2 mockups + handoff prompt.
/// Mirrors window.SID.tokens in the React prototype.
class SidColors {
  // Surfaces
  static const land = Color(0xFFF3EDE0); // cream
  static const ocean = Color(0xFFDDE7F0); // pale blue
  static const card = Color(0xFFFFFFFF);
  static const bgPage = Color(0xFFF3EDE0); // page bg outside the map

  // Ink ladder
  static const ink = Color(0xFF0F172A); // primary text, status bar, selected pin
  static const ink2 = Color(0xFF475569); // secondary text
  static const ink3 = Color(0xFF94A3B8); // tertiary / metadata
  static const hairline = Color(0xFFE2E8F0);

  // Accents
  static const live = Color(0xFF22C55E);
  static const selected = Color(0xFF0F172A);
  static const warning = Color(0xFFF59E0B);
  static const logoRed = Color(0xFFDC2626);

  // Slightly varied ocean tones for the map gradient (very subtle)
  static const oceanShallow = Color(0xFFE5ECF3);
  static const oceanDeep = Color(0xFFCFDDE9);

  // Map overlay tints (light theme)
  static const mpaFill = Color(0x33B89D6E); // warm sand at low alpha
  static const mpaEdge = Color(0x80A07A40);
  static const landEdge = Color(0xFFC9BFA8); // cream-darker hairline
  static const graticule = Color(0x33475569); // ink2 at low alpha
}

/// {value, color} stop. `value` is in the layer's user-facing unit
/// (kt for wind, ft for swell/vis, °F for sst, mg/m³ for chl).
class SidStop {
  final double value;
  final Color color;
  final String label;
  const SidStop(this.value, this.color, this.label);
}

class SidPalettes {
  // Wind (kt) — calm → gale
  static const List<SidStop> wind = [
    SidStop(0, Color(0xFFCFDDED), 'calm'),
    SidStop(5, Color(0xFFCFDDED), '5'),
    SidStop(10, Color(0xFFB3D3A3), '10'),
    SidStop(15, Color(0xFFEAD68A), '15'),
    SidStop(20, Color(0xFFE8A374), '20'),
    SidStop(35, Color(0xFFC47A8A), 'gale'),
  ];

  // Swell Hs (ft) — Glassy → Storm
  static const List<SidStop> swell = [
    SidStop(0, Color(0xFFD1E8F5), 'Glassy'),
    SidStop(2, Color(0xFFA8D4E8), 'Calm'),
    SidStop(4, Color(0xFF9BCBB1), 'Workable'),
    SidStop(6, Color(0xFFEAD68A), 'Sketchy'),
    SidStop(8, Color(0xFFE8A374), 'Big'),
    SidStop(12, Color(0xFFC47A8A), 'XL'),
    SidStop(16, Color(0xFF8B3A3A), 'Storm'),
  ];

  // Sea temp (°F) — cool blue → green → yellow
  static const List<SidStop> sst = [
    SidStop(50, Color(0xFF5B8DB5), '52'),
    SidStop(56, Color(0xFF7FA3C0), '56'),
    SidStop(60, Color(0xFF9BCBB1), '60'),
    SidStop(64, Color(0xFFC8D68A), '64'),
    SidStop(68, Color(0xFFEAD68A), '68'),
  ];

  // Chlorophyll (mg/m³) — low chl is best for divers (gin clear)
  static const List<SidStop> chl = [
    SidStop(0.10, Color(0xFF1F3A55), 'Gin'),
    SidStop(0.30, Color(0xFF2D5478), 'Blue'),
    SidStop(0.80, Color(0xFF5B8DB5), 'Clear'),
    SidStop(2.00, Color(0xFF7FA05A), 'Green'),
    SidStop(5.00, Color(0xFF7A5A3C), 'Murky'),
  ];

  // Visibility (ft) — poor → gin clear (model output)
  static const List<SidStop> vis = [
    SidStop(5, Color(0xFFA8B8C8), 'Poor'),
    SidStop(15, Color(0xFF7FA3C0), 'Fair'),
    SidStop(25, Color(0xFF5B8DB5), 'Workable'),
    SidStop(35, Color(0xFF3A6E95), 'Good'),
    SidStop(50, Color(0xFF1F4D75), 'Excellent'),
  ];
}

/// Type system. Family is left null so platform default fonts apply
/// (San Francisco on iOS, Roboto on Android — both close enough to the
/// prototype's "-apple-system, SF Pro Text, Helvetica Neue" stack).
class SidType {
  static const displayNum = TextStyle(
    fontSize: 32, fontWeight: FontWeight.w700, letterSpacing: -1.2,
    color: SidColors.ink,
  );
  static const bigNum = TextStyle(
    fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: -1.0,
    color: SidColors.ink,
  );
  static const title = TextStyle(
    fontSize: 19, fontWeight: FontWeight.w700, letterSpacing: -0.3,
    color: SidColors.ink,
  );
  static const body = TextStyle(
    fontSize: 14, fontWeight: FontWeight.w500,
    color: SidColors.ink,
  );
  static const chipLabel = TextStyle(
    fontSize: 13, fontWeight: FontWeight.w700,
    color: SidColors.ink,
  );
  static const pill = TextStyle(
    fontSize: 11.5, fontWeight: FontWeight.w600,
    color: SidColors.ink,
  );
  static const caption = TextStyle(
    fontSize: 12, fontWeight: FontWeight.w500,
    color: SidColors.ink2,
  );
  static const eyebrow = TextStyle(
    fontSize: 9.5, fontWeight: FontWeight.w700,
    letterSpacing: 0.7,
    color: SidColors.ink3,
  );
  static const mono = TextStyle(
    fontSize: 11.5, fontFamily: 'monospace', fontWeight: FontWeight.w500,
    color: SidColors.ink2,
  );
}

class SidRadius {
  static const card = 12.0;
  static const colorBar = 10.0;
  static const segmented = 8.0;
  static const tooltipBadge = 6.0;
  static const spotRow = 9.0;
}

class SidSpacing {
  static const pageH = 18.0;
  static const mapPadH = 14.0;
}

/// Display-side layer spec — the label + unit shown on chips and headers.
/// `id` matches the manifest layer id (sst/chl/wind/wave/viz).
class SidLayerSpec {
  final String id;
  final String label;
  final String unit;
  const SidLayerSpec({required this.id, required this.label, required this.unit});
}

class SidLayers {
  static const sst = SidLayerSpec(id: 'sst', label: 'Temp', unit: '°F');
  static const chl = SidLayerSpec(id: 'chl', label: 'Chl', unit: 'mg/m³');
  static const wind = SidLayerSpec(id: 'wind', label: 'Wind', unit: 'kt');
  static const wave = SidLayerSpec(id: 'wave', label: 'Swell', unit: 'ft Hs');
  static const viz = SidLayerSpec(id: 'viz', label: 'Vis', unit: 'ft');

  /// Canonical chip-row order from the v2 mockups (Temp · Chl · Wind · Swell · Vis).
  static const List<SidLayerSpec> all = [sst, chl, wind, wave, viz];

  static SidLayerSpec? forId(String id) {
    for (final l in all) {
      if (l.id == id) return l;
    }
    // Aliases used elsewhere in the codebase
    if (id == 'swell') return wave;
    if (id == 'vis') return viz;
    if (id == 'temp') return sst;
    return null;
  }
}

/// Source labels shown right-aligned on the overlay row.
class SidSources {
  static String? forId(String id) {
    switch (id) {
      case 'sst':
      case 'temp':
        return 'VIIRS · 750 m';
      case 'chl':
        return 'OC-CCI · 4 km';
      case 'wind':
        return 'NOAA HRRR · 3 km';
      case 'wave':
      case 'swell':
        return 'NOAA WW3 · 0.16°';
      case 'viz':
      case 'vis':
        return 'SID Model · 1.5 km';
      default:
        return null;
    }
  }
}
