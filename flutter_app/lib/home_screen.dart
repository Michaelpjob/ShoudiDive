import 'dart:math' as math;
import 'package:flutter/material.dart';

import 'bbox.dart';
import 'forecast.dart';
import 'geojson_loader.dart';
import 'grid.dart';
import 'manifest.dart';
import 'map_view.dart';
import 'static_data.dart';
import 'time_slider.dart';
import 'wind_particles.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Manifest? _manifest;
  GeoFeatureCollection? _land;
  GeoFeatureCollection? _mpa;
  ForecastSummary? _windSummary;
  ForecastSummary? _swellSummary;
  Object? _error;

  bool _showMpa = true;
  bool _showLabels = true;
  bool _showSpots = true;
  double _layerOpacity = 0.85;

  String _layerId = 'sst';
  String _windowKey = '1d';
  int _windSlotIdx = 0;
  int _swellSlotIdx = 0;

  Grid? _layerGrid;
  UVGrid? _uvGrid;
  bool _loadingGrid = false;
  Object? _gridLoadKey;

  final Map<String, UVGrid> _uvCache = {};
  final Map<String, WaveGrid> _waveCache = {};

  MapPin? _pin;

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    try {
      final manifest = await fetchManifest();
      if (!mounted) return;
      setState(() => _manifest = manifest);
      // Fire summary + geojson fetches in parallel; none of them are fatal
      // for the rest of the app, so swallow errors individually.
      final futures = <Future<void>>[
        fetchFeatureCollection(kLandUrl).then(
            (v) => mounted ? setState(() => _land = v) : null,
            onError: (_) {}),
        fetchFeatureCollection(kMpaUrl).then(
            (v) => mounted ? setState(() => _mpa = v) : null,
            onError: (_) {}),
        if (manifest.wind5d != null)
          fetchWindSummary(manifest.wind5d!).then(
              (v) {
                if (!mounted) return;
                setState(() {
                  _windSummary = v;
                  _windSlotIdx = _findNowIdx(v);
                });
                if (_layerId == 'wind') _loadCurrentGrid();
              },
              onError: (e) => debugPrint('wind summary failed: $e')),
        if (manifest.swell5d != null)
          fetchSwellSummary(manifest.swell5d!).then(
              (v) {
                if (!mounted) return;
                setState(() {
                  _swellSummary = v;
                  _swellSlotIdx = _findNowIdx(v);
                });
                if (_layerId == 'wave') _loadCurrentGrid();
              },
              onError: (e) => debugPrint('swell summary failed: $e')),
      ];
      _loadCurrentGrid();
      await Future.wait(futures);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    }
  }

  // Choose a reasonable initial slider position: the bucket whose hours
  // straddle "now" (in the summary's tz), falling back to the 0th slot.
  int _findNowIdx(ForecastSummary summary) {
    if (summary.slots.isEmpty) return 0;
    // Use UTC since we don't know the offset exactly. Pick the slot
    // whose date matches today's UTC date and whose mid-hour is closest
    // to now's UTC hour minus 7 (rough PT). Good enough for a default.
    final now = DateTime.now().toUtc();
    final approxLocalHour = (now.hour - 7) % 24;
    final today = '${now.year}-'
        '${now.month.toString().padLeft(2, '0')}-'
        '${now.day.toString().padLeft(2, '0')}';
    int bestIdx = 0;
    int bestDist = 1 << 30;
    for (var i = 0; i < summary.slots.length; i++) {
      final s = summary.slots[i];
      if (s.date != today) continue;
      final dist = (s.bucketDef.midHourLocal - approxLocalHour).abs();
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  Future<void> _loadCurrentGrid() async {
    final manifest = _manifest;
    if (manifest == null) return;
    final layer = manifest.layers[_layerId];
    if (layer == null) return;

    final key = Object();
    _gridLoadKey = key;
    setState(() => _loadingGrid = true);

    try {
      Grid grid;
      UVGrid? uvGrid;

      if (layer.id == 'wind' && _windSummary != null) {
        final slot = _windSummary!.slots[_windSlotIdx
            .clamp(0, _windSummary!.slots.length - 1)];
        final url = slot.absoluteGridUrl;
        UVGrid uv;
        if (_uvCache.containsKey(url)) {
          uv = _uvCache[url]!;
        } else {
          uv = await fetchUVGrid(url, uvLo: -30, uvHi: 30);
          _uvCache[url] = uv;
        }
        grid = uv.speedGrid;
        uvGrid = uv;
      } else if (layer.id == 'wave' && _swellSummary != null) {
        final slot = _swellSummary!.slots[_swellSlotIdx
            .clamp(0, _swellSummary!.slots.length - 1)];
        final url = slot.absoluteGridUrl;
        WaveGrid wave;
        if (_waveCache.containsKey(url)) {
          wave = _waveCache[url]!;
        } else {
          final src = manifest.swell5d!;
          wave = await fetchWaveGrid(
            url,
            hLo: src.primaryRange[0],
            hHi: src.primaryRange[1],
            pLo: src.secondaryRange?[0] ?? 0.0,
            pHi: src.secondaryRange?[1] ?? 25.0,
          );
          _waveCache[url] = wave;
        }
        grid = wave.hsGrid;
      } else {
        // Fallback path: chip-based windows for layers without 5d data
        // (sst, chl, viz, or wind/wave when summary fetch failed).
        if (!layer.windows.containsKey(_windowKey)) {
          _windowKey = layer.windows.keys.first;
        }
        final window = layer.windows[_windowKey]!;
        final range = layer.range;
        if (range == null || range.length != 2) {
          if (mounted && _gridLoadKey == key) {
            setState(() {
              _layerGrid = null;
              _uvGrid = null;
              _loadingGrid = false;
            });
          }
          return;
        }
        if (layer.kind == LayerKind.wave) {
          final pRange = layer.rangePeriodS ?? const [0.0, 25.0];
          final wave = await fetchWaveGrid(
            window.absoluteUrl,
            hLo: range[0],
            hHi: range[1],
            pLo: pRange[0],
            pHi: pRange[1],
          );
          grid = wave.hsGrid;
        } else if (layer.id == 'wind' && window.absoluteUvUrl != null) {
          final uv = await fetchUVGrid(window.absoluteUvUrl!,
              uvLo: -30, uvHi: 30);
          grid = uv.speedGrid;
          uvGrid = uv;
        } else {
          grid = await fetchScalarGrid(window.absoluteUrl,
              lo: range[0], hi: range[1], scale: layer.scale);
        }
      }

      if (!mounted || _gridLoadKey != key) return;
      setState(() {
        _layerGrid = grid;
        _uvGrid = uvGrid;
        _loadingGrid = false;
      });
    } catch (e) {
      if (!mounted || _gridLoadKey != key) return;
      setState(() {
        _layerGrid = null;
        _uvGrid = null;
        _loadingGrid = false;
        _error = e;
      });
    }
  }

  void _selectLayer(String id) {
    setState(() {
      _layerId = id;
      _layerGrid = null;
      _uvGrid = null;
    });
    _loadCurrentGrid();
  }

  void _selectWindow(String key) {
    setState(() {
      _windowKey = key;
      _layerGrid = null;
    });
    _loadCurrentGrid();
  }

  void _setWindSlot(int idx) {
    if (idx == _windSlotIdx) return;
    setState(() => _windSlotIdx = idx);
    _loadCurrentGrid();
  }

  void _setSwellSlot(int idx) {
    if (idx == _swellSlotIdx) return;
    setState(() => _swellSlotIdx = idx);
    _loadCurrentGrid();
  }

  void _openSettings() {
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setSheetState) {
            return SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Display',
                        style: Theme.of(ctx).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        const SizedBox(
                            width: 96, child: Text('Layer opacity')),
                        Expanded(
                          child: Slider(
                            min: 0.3,
                            max: 1.0,
                            value: _layerOpacity,
                            onChanged: (v) {
                              setSheetState(() {});
                              setState(() => _layerOpacity = v);
                            },
                          ),
                        ),
                        SizedBox(
                          width: 38,
                          child: Text(
                            '${(_layerOpacity * 100).round()}%',
                            textAlign: TextAlign.right,
                          ),
                        ),
                      ],
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Marine Protected Areas'),
                      value: _showMpa,
                      onChanged: (v) {
                        setSheetState(() {});
                        setState(() => _showMpa = v);
                      },
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Place labels'),
                      value: _showLabels,
                      onChanged: (v) {
                        setSheetState(() {});
                        setState(() => _showLabels = v);
                      },
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Saved spots'),
                      value: _showSpots,
                      onChanged: (v) {
                        setSheetState(() {});
                        setState(() => _showSpots = v);
                      },
                    ),
                    const SizedBox(height: 8),
                    Text('Saved spots',
                        style: Theme.of(ctx).textTheme.titleMedium),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final s in kSavedSpots)
                          ActionChip(
                            label: Text(s.name),
                            onPressed: () {
                              Navigator.of(ctx).pop();
                              setState(() => _pin = MapPin(s.lng, s.lat));
                            },
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final manifest = _manifest;
    return Scaffold(
      appBar: AppBar(
        title: const Text('ShoudiDive'),
        actions: [
          IconButton(
            tooltip: 'Settings',
            onPressed: _openSettings,
            icon: const Icon(Icons.tune),
          ),
          IconButton(
            tooltip: 'Refresh',
            onPressed: _bootstrap,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _error != null
          ? _ErrorView(error: _error.toString(), onRetry: _bootstrap)
          : manifest == null
              ? const Center(child: CircularProgressIndicator())
              : _buildLoaded(manifest),
    );
  }

  Widget _buildLoaded(Manifest manifest) {
    final layer = manifest.layers[_layerId];
    if (layer == null) {
      return const Center(child: Text('Layer not found in manifest'));
    }
    final mapper = colorMapperForLayerId(layer.id);
    final spec = _layerGrid == null
        ? null
        : MapLayerSpec(grid: _layerGrid!, colorMapper: mapper);

    final usingWindSlider = layer.id == 'wind' && _windSummary != null;
    final usingSwellSlider = layer.id == 'wave' && _swellSummary != null;

    return SafeArea(
      child: Column(
        children: [
          SizedBox(
            height: 44,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              children: [
                for (final l in manifest.layers.values) ...[
                  ChoiceChip(
                    label: Text(l.name),
                    selected: l.id == _layerId,
                    onSelected: (_) => _selectLayer(l.id),
                  ),
                  const SizedBox(width: 6),
                ],
              ],
            ),
          ),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Stack(
                children: [
                  Positioned.fill(
                    child: Container(
                      decoration: BoxDecoration(
                        border: Border.all(color: Colors.white24),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      clipBehavior: Clip.antiAlias,
                      child: Stack(
                        children: [
                          Positioned.fill(
                            child: MapView(
                              bbox: manifest.bbox,
                              layer: spec,
                              land: _land,
                              mpa: _showMpa ? _mpa : null,
                              opacity: _layerOpacity,
                              showLabels: _showLabels,
                              showSpots: _showSpots,
                              pin: _pin,
                              onTapPoint: (lng, lat) =>
                                  setState(() => _pin = MapPin(lng, lat)),
                            ),
                          ),
                          if (layer.id == 'wind' && _uvGrid != null)
                            Positioned.fill(
                              child: IgnorePointer(
                                child: WindParticlesOverlay(
                                  bbox: manifest.bbox,
                                  uv: _uvGrid!,
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                  if (_loadingGrid)
                    const Positioned(
                      top: 8,
                      right: 8,
                      child: SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    ),
                  Positioned(
                    left: 8,
                    bottom: 8,
                    child: _Legend(layer: layer),
                  ),
                  if (_pin != null)
                    Positioned(
                      right: 8,
                      top: 8,
                      child: _PinReadout(
                        pin: _pin!,
                        layer: layer,
                        bbox: manifest.bbox,
                        grid: _layerGrid,
                        onClear: () => setState(() => _pin = null),
                      ),
                    ),
                ],
              ),
            ),
          ),
          if (usingWindSlider)
            TimeSlider(
              summary: _windSummary!,
              activeIndex: _windSlotIdx,
              onChanged: _setWindSlot,
              unit: 'kt',
            )
          else if (usingSwellSlider)
            TimeSlider(
              summary: _swellSummary!,
              activeIndex: _swellSlotIdx,
              onChanged: _setSwellSlot,
              unit: 'm',
            )
          else if (layer.windows.length > 1)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Wrap(
                spacing: 8,
                children: layer.windows.keys.map((k) {
                  return ChoiceChip(
                    label: Text(k),
                    selected: k == _windowKey,
                    onSelected: (_) => _selectWindow(k),
                  );
                }).toList(),
              ),
            ),
          _InfoPanel(
            layer: layer,
            window: layer.windows[_windowKey] ?? layer.windows.values.first,
            manifest: manifest,
          ),
        ],
      ),
    );
  }
}

class _Legend extends StatelessWidget {
  final Layer layer;
  const _Legend({required this.layer});

  @override
  Widget build(BuildContext context) {
    final range = layer.range;
    if (range == null || range.length != 2) return const SizedBox.shrink();
    final mapper = colorMapperForLayerId(layer.id);
    final stops = List<Color>.generate(40, (i) {
      final t = i / 39.0;
      double v;
      if (layer.id == 'chl') {
        final lo = range[0];
        final hi = range[1];
        v = lo * math.pow(hi / lo, t).toDouble();
      } else {
        v = range[0] + (range[1] - range[0]) * t;
      }
      return mapper(v);
    });
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(range[0].toStringAsFixed(0),
              style: const TextStyle(color: Colors.white70, fontSize: 11)),
          const SizedBox(width: 6),
          Container(
            width: 120,
            height: 8,
            decoration: BoxDecoration(
              gradient: LinearGradient(colors: stops),
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 6),
          Text('${range[1].toStringAsFixed(0)} ${layer.unit}',
              style: const TextStyle(color: Colors.white70, fontSize: 11)),
        ],
      ),
    );
  }
}

class _PinReadout extends StatelessWidget {
  final MapPin pin;
  final Layer layer;
  final BBox bbox;
  final Grid? grid;
  final VoidCallback onClear;

  const _PinReadout({
    required this.pin,
    required this.layer,
    required this.bbox,
    required this.grid,
    required this.onClear,
  });

  String _format(double v) {
    if (v.isNaN) return '—';
    switch (layer.id) {
      case 'chl':
        if (v < 1) return v.toStringAsFixed(2);
        if (v < 10) return v.toStringAsFixed(1);
        return v.toStringAsFixed(0);
      case 'sst':
        final f = v * 9 / 5 + 32;
        return '${v.toStringAsFixed(1)}°C / ${f.toStringAsFixed(0)}°F';
      case 'viz':
        return v.toStringAsFixed(0);
      case 'wind':
        return v.toStringAsFixed(1);
      case 'wave':
        final ft = v * 3.28084;
        return '${v.toStringAsFixed(1)} m / ${ft.toStringAsFixed(0)} ft';
      default:
        return v.toStringAsFixed(1);
    }
  }

  @override
  Widget build(BuildContext context) {
    final v = grid?.sampleAt(bbox, pin.lng, pin.lat) ?? double.nan;
    final formatted = _format(v);
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 220),
      child: Card(
        color: Colors.black.withValues(alpha: 0.75),
        margin: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(10, 8, 4, 8),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(layer.name,
                        style: const TextStyle(
                            color: Colors.white70,
                            fontSize: 11,
                            fontWeight: FontWeight.w500)),
                    Text(
                      layer.id == 'sst' || layer.id == 'wave'
                          ? formatted
                          : '$formatted ${layer.unit}',
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '${pin.lat.toStringAsFixed(3)}°N, '
                      '${(-pin.lng).toStringAsFixed(3)}°W',
                      style: const TextStyle(
                          color: Colors.white60,
                          fontSize: 11,
                          fontFamily: 'monospace'),
                    ),
                  ],
                ),
              ),
              IconButton(
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.close, color: Colors.white70, size: 16),
                onPressed: onClear,
                tooltip: 'Clear pin',
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _InfoPanel extends StatelessWidget {
  final Layer layer;
  final LayerWindow window;
  final Manifest manifest;
  const _InfoPanel({
    required this.layer,
    required this.window,
    required this.manifest,
  });

  @override
  Widget build(BuildContext context) {
    final range = layer.range;
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 4, 12, 12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(layer.name, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('Window: ${window.label}'),
            if (range != null)
              Text('Range: ${range[0]}–${range[1]} ${layer.unit}'),
            Text(
              'Manifest generated: ${manifest.generatedAt.toLocal()}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String error;
  final VoidCallback onRetry;
  const _ErrorView({required this.error, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 48),
            const SizedBox(height: 12),
            Text(error, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}
