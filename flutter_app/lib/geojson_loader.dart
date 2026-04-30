import 'dart:convert';
import 'package:http/http.dart' as http;

class Polygon {
  final List<List<List<double>>> rings;
  Polygon(this.rings);
}

class GeoFeatureCollection {
  final List<Polygon> polygons;
  GeoFeatureCollection(this.polygons);
}

GeoFeatureCollection parseFeatureCollection(Map<String, dynamic> json) {
  final polys = <Polygon>[];
  final features = (json['features'] as List?) ?? const [];
  for (final f in features) {
    final geom = (f as Map<String, dynamic>)['geometry'] as Map<String, dynamic>?;
    if (geom == null) continue;
    final type = geom['type'];
    final coords = geom['coordinates'];
    if (type == 'Polygon') {
      polys.add(Polygon((coords as List)
          .map((r) => (r as List)
              .map((p) => (p as List).cast<num>().map((n) => n.toDouble()).toList())
              .toList())
          .toList()));
    } else if (type == 'MultiPolygon') {
      for (final poly in coords as List) {
        polys.add(Polygon((poly as List)
            .map((r) => (r as List)
                .map((p) => (p as List).cast<num>().map((n) => n.toDouble()).toList())
                .toList())
            .toList()));
      }
    }
  }
  return GeoFeatureCollection(polys);
}

Future<GeoFeatureCollection> fetchFeatureCollection(String url) async {
  final res = await http.get(Uri.parse(url));
  if (res.statusCode != 200) {
    throw Exception('HTTP ${res.statusCode} for $url');
  }
  return parseFeatureCollection(jsonDecode(res.body) as Map<String, dynamic>);
}
