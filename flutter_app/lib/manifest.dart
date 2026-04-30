import 'dart:convert';
import 'package:http/http.dart' as http;
import 'bbox.dart';
import 'grid.dart';

const String kBaseUrl = 'https://shouldidive.com';
const String kManifestUrl = '$kBaseUrl/data/manifest.json';
const String kLandUrl = '$kBaseUrl/data/land.geojson';
const String kMpaUrl = '$kBaseUrl/data/mpa-boundaries.geojson';

class LayerWindow {
  final String key;
  final String url;
  final String? uvUrl;
  final List<String> dates;
  final String? validAt;

  LayerWindow({
    required this.key,
    required this.url,
    this.uvUrl,
    this.dates = const [],
    this.validAt,
  });

  String get absoluteUrl => url.startsWith('http') ? url : '$kBaseUrl$url';
  String? get absoluteUvUrl =>
      uvUrl == null ? null : (uvUrl!.startsWith('http') ? uvUrl : '$kBaseUrl$uvUrl');

  String get label {
    if (dates.isNotEmpty) {
      if (dates.length == 1) return dates.first;
      return '${dates.first} — ${dates.last}';
    }
    if (validAt != null) return validAt!;
    return key;
  }
}

enum LayerKind { scalar, wave }

class Layer {
  final String id;
  final String name;
  final String unit;
  final List<double>? range;
  final List<double>? rangePeriodS;
  final GridScale scale;
  final LayerKind kind;
  final Map<String, LayerWindow> windows;

  Layer({
    required this.id,
    required this.name,
    required this.unit,
    required this.range,
    required this.scale,
    required this.kind,
    required this.windows,
    this.rangePeriodS,
  });
}

class ForecastSource {
  final String summaryUrl;
  final List<double> primaryRange; // wind: speed_range; swell: height_range_m
  final List<double>? secondaryRange; // wind: uv_range; swell: period_range_s
  ForecastSource({
    required this.summaryUrl,
    required this.primaryRange,
    this.secondaryRange,
  });

  String get absoluteSummaryUrl =>
      summaryUrl.startsWith('http') ? summaryUrl : '$kBaseUrl$summaryUrl';
}

class Manifest {
  final DateTime generatedAt;
  final BBox bbox;
  final Map<String, Layer> layers;
  final ForecastSource? wind5d;
  final ForecastSource? swell5d;

  Manifest({
    required this.generatedAt,
    required this.bbox,
    required this.layers,
    this.wind5d,
    this.swell5d,
  });

  static const _spec = <String, ({String name, String unit, LayerKind kind})>{
    'sst':  (name: 'Sea Surface Temp', unit: '°C',     kind: LayerKind.scalar),
    'chl':  (name: 'Chlorophyll',      unit: 'mg/m³',  kind: LayerKind.scalar),
    'viz':  (name: 'Water Visibility', unit: 'ft',     kind: LayerKind.scalar),
    'wind': (name: 'Wind',             unit: 'kt',     kind: LayerKind.scalar),
    'wave': (name: 'Waves',            unit: 'm',      kind: LayerKind.wave),
  };

  factory Manifest.fromJson(Map<String, dynamic> j) {
    final rawLayers = j['layers'] as Map<String, dynamic>;
    final layers = <String, Layer>{};
    for (final entry in _spec.entries) {
      final raw = rawLayers[entry.key];
      if (raw == null) continue;
      final windowsRaw = (raw['windows'] as Map<String, dynamic>?) ?? {};
      final windows = <String, LayerWindow>{};
      windowsRaw.forEach((k, v) {
        final m = v as Map<String, dynamic>;
        final url = (m['url'] ?? m['speed_url']) as String?;
        if (url == null) return;
        windows[k] = LayerWindow(
          key: k,
          url: url,
          uvUrl: m['uv_url'] as String?,
          dates: (m['dates'] as List?)?.cast<String>() ?? const [],
          validAt: m['valid_at'] as String?,
        );
      });
      if (windows.isEmpty) continue;
      List<double>? range;
      if (raw['range'] != null) {
        range = (raw['range'] as List).cast<num>().map((n) => n.toDouble()).toList();
      } else if (raw['range_ft'] != null) {
        range = (raw['range_ft'] as List).cast<num>().map((n) => n.toDouble()).toList();
      } else if (raw['speed_range'] != null) {
        range = (raw['speed_range'] as List).cast<num>().map((n) => n.toDouble()).toList();
      } else if (raw['height_range_m'] != null) {
        range = (raw['height_range_m'] as List).cast<num>().map((n) => n.toDouble()).toList();
      }
      List<double>? rangeP;
      if (raw['period_range_s'] != null) {
        rangeP = (raw['period_range_s'] as List).cast<num>().map((n) => n.toDouble()).toList();
      }
      final scale = (raw['scale'] == 'log10') ? GridScale.log10 : GridScale.linear;
      layers[entry.key] = Layer(
        id: entry.key,
        name: entry.value.name,
        unit: entry.value.unit,
        range: range,
        rangePeriodS: rangeP,
        scale: scale,
        kind: entry.value.kind,
        windows: windows,
      );
    }
    ForecastSource? wind5d;
    final w5 = rawLayers['wind5d'];
    if (w5 is Map<String, dynamic> && w5['summary_url'] is String) {
      wind5d = ForecastSource(
        summaryUrl: w5['summary_url'] as String,
        primaryRange: (w5['speed_range'] as List?)
                ?.cast<num>()
                .map((n) => n.toDouble())
                .toList() ??
            const [0.0, 50.0],
        secondaryRange: (w5['uv_range'] as List?)
            ?.cast<num>()
            .map((n) => n.toDouble())
            .toList(),
      );
    }
    ForecastSource? swell5d;
    final s5 = rawLayers['swell5d'];
    if (s5 is Map<String, dynamic> && s5['summary_url'] is String) {
      swell5d = ForecastSource(
        summaryUrl: s5['summary_url'] as String,
        primaryRange: (s5['height_range_m'] as List?)
                ?.cast<num>()
                .map((n) => n.toDouble())
                .toList() ??
            const [0.0, 12.0],
        secondaryRange: (s5['period_range_s'] as List?)
            ?.cast<num>()
            .map((n) => n.toDouble())
            .toList() ??
            const [0.0, 25.0],
      );
    }
    return Manifest(
      generatedAt: DateTime.parse(j['generated_at'] as String),
      bbox: BBox.fromList(j['bbox'] as List),
      layers: layers,
      wind5d: wind5d,
      swell5d: swell5d,
    );
  }
}

Future<Manifest> fetchManifest({http.Client? client}) async {
  final c = client ?? http.Client();
  final res = await c.get(Uri.parse(kManifestUrl));
  if (res.statusCode != 200) {
    throw Exception('Manifest fetch failed: HTTP ${res.statusCode}');
  }
  return Manifest.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
}
