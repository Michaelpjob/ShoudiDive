import 'dart:convert';
import 'package:http/http.dart' as http;

import 'manifest.dart';

class ForecastBucketDef {
  final String name;
  final int startHourLocal;
  final int endHourLocal;
  ForecastBucketDef(this.name, this.startHourLocal, this.endHourLocal);

  int get midHourLocal => ((startHourLocal + endHourLocal) / 2).round();
}

class ForecastBucket {
  final int day;
  final String bucket;
  final List<int> hours;
  final String date;
  final String dayLabel;
  final String weekday;
  final String? confidence;
  final double? meanPrimary;
  final double? minPrimary;
  final double? maxPrimary;
  final double? meanDirDeg;
  final String gridUrl;
  final ForecastBucketDef bucketDef;

  ForecastBucket({
    required this.day,
    required this.bucket,
    required this.hours,
    required this.date,
    required this.dayLabel,
    required this.weekday,
    required this.confidence,
    required this.meanPrimary,
    required this.minPrimary,
    required this.maxPrimary,
    required this.meanDirDeg,
    required this.gridUrl,
    required this.bucketDef,
  });

  String get absoluteGridUrl =>
      gridUrl.startsWith('http') ? gridUrl : '$kBaseUrl$gridUrl';

  /// Approximate UTC anchor for this bucket. Date is in the summary's tz
  /// (PT) and we use the bucket's local mid-hour. We don't have offset info
  /// in the JSON so assume PDT (UTC-7) or PST (UTC-8) based on month —
  /// good enough for label display, not for safety-critical timing.
  DateTime localAnchor() {
    final parts = date.split('-').map(int.parse).toList();
    return DateTime(parts[0], parts[1], parts[2], bucketDef.midHourLocal);
  }
}

class ForecastSummary {
  final String anchorDate;
  final String tz;
  final List<ForecastBucketDef> bucketsDef;
  final List<ForecastBucket> slots;
  ForecastSummary({
    required this.anchorDate,
    required this.tz,
    required this.bucketsDef,
    required this.slots,
  });
}

ForecastSummary _parseSummary(Map<String, dynamic> j, {required String gridUrlKey}) {
  final tz = j['tz'] as String? ?? 'America/Los_Angeles';
  final anchor = j['anchor_date'] as String;
  final bucketsDef = (j['buckets_def'] as List?)
          ?.map((b) {
            final m = b as Map<String, dynamic>;
            return ForecastBucketDef(
              m['name'] as String,
              m['start_hour_local'] as int,
              m['end_hour_local'] as int,
            );
          })
          .toList() ??
      [];
  final byName = {for (final b in bucketsDef) b.name: b};

  final slots = <ForecastBucket>[];
  final days = (j['days'] as List?) ?? const [];
  for (final d in days) {
    final dm = d as Map<String, dynamic>;
    final day = dm['day'] as int;
    final date = dm['date'] as String;
    final dayLabel = dm['label'] as String? ?? '';
    final weekday = dm['weekday'] as String? ?? '';
    final confidence = dm['confidence'] as String?;
    final dayBuckets = (dm['buckets'] as List?) ?? const [];
    for (final b in dayBuckets) {
      final bm = b as Map<String, dynamic>;
      final url = bm[gridUrlKey] as String?;
      if (url == null) continue;
      final bucketName = bm['bucket'] as String;
      final def = byName[bucketName];
      if (def == null) continue;
      slots.add(ForecastBucket(
        day: day,
        bucket: bucketName,
        hours: (bm['hours'] as List?)?.cast<int>() ?? const [],
        date: date,
        dayLabel: dayLabel,
        weekday: weekday,
        confidence: confidence,
        meanPrimary: (bm['mean_kt'] ?? bm['mean_hs_m'] ?? bm['mean_hs_ft'])
            ?.toDouble(),
        minPrimary: (bm['min_kt'] ?? bm['min_hs_ft'])?.toDouble(),
        maxPrimary: (bm['max_kt'] ?? bm['max_hs_ft'])?.toDouble(),
        meanDirDeg: (bm['mean_dir_deg'] ?? bm['mean_dp_deg'])?.toDouble(),
        gridUrl: url,
        bucketDef: def,
      ));
    }
  }
  return ForecastSummary(
    anchorDate: anchor,
    tz: tz,
    bucketsDef: bucketsDef,
    slots: slots,
  );
}

Future<ForecastSummary> fetchWindSummary(ForecastSource src) async {
  final res = await http.get(Uri.parse(src.absoluteSummaryUrl));
  if (res.statusCode != 200) {
    throw Exception('wind summary HTTP ${res.statusCode}');
  }
  return _parseSummary(jsonDecode(res.body) as Map<String, dynamic>,
      gridUrlKey: 'uv_url');
}

Future<ForecastSummary> fetchSwellSummary(ForecastSource src) async {
  final res = await http.get(Uri.parse(src.absoluteSummaryUrl));
  if (res.statusCode != 200) {
    throw Exception('swell summary HTTP ${res.statusCode}');
  }
  return _parseSummary(jsonDecode(res.body) as Map<String, dynamic>,
      gridUrlKey: 'wave_url');
}
