import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/services.dart' show rootBundle;
import 'package:http/http.dart' as http;

import 'bbox.dart';

class Grid {
  final int width;
  final int height;
  final Float32List data;
  const Grid(this.width, this.height, this.data);

  double sampleNearest(double fx, double fy) {
    if (fx < 0 || fx > width - 1 || fy < 0 || fy > height - 1) return double.nan;
    final x = fx.round().clamp(0, width - 1);
    final y = fy.round().clamp(0, height - 1);
    return data[y * width + x];
  }

  double sampleAt(BBox bbox, double lng, double lat) {
    final fx = ((lng - bbox.lngMin) / bbox.widthDeg) * (width - 1);
    final fy = ((bbox.latMax - lat) / bbox.heightDeg) * (height - 1);
    return sampleBilinear(fx, fy);
  }

  double sampleBilinear(double fx, double fy) {
    if (fx < 0 || fx > width - 1 || fy < 0 || fy > height - 1) return double.nan;
    final x0 = fx.floor();
    final x1 = math.min(x0 + 1, width - 1);
    final y0 = fy.floor();
    final y1 = math.min(y0 + 1, height - 1);
    final tx = fx - x0;
    final ty = fy - y0;
    final v00 = data[y0 * width + x0];
    final v10 = data[y0 * width + x1];
    final v01 = data[y1 * width + x0];
    final v11 = data[y1 * width + x1];
    final vs = [v00, v10, v01, v11];
    double sum = 0;
    int n = 0;
    for (final v in vs) {
      if (!v.isNaN) { sum += v; n++; }
    }
    if (n == 0) return double.nan;
    if (n < 4) return sum / n;
    return v00 * (1 - tx) * (1 - ty) +
        v10 * tx * (1 - ty) +
        v01 * (1 - tx) * ty +
        v11 * tx * ty;
  }
}

enum GridScale { linear, log10 }

Future<Uint8List> _fetchBytes(String url) async {
  final res = await http.get(Uri.parse(url));
  if (res.statusCode != 200) {
    throw Exception('HTTP ${res.statusCode} for $url');
  }
  return res.bodyBytes;
}

Future<ui.Image> _decodeImage(Uint8List bytes) async {
  final codec = await ui.instantiateImageCodec(bytes);
  final frame = await codec.getNextFrame();
  return frame.image;
}

Future<Uint8List> _readRawRgba(ui.Image img) async {
  final bd = await img.toByteData(format: ui.ImageByteFormat.rawRgba);
  if (bd == null) throw Exception('Failed to read image bytes');
  return bd.buffer.asUint8List();
}

Future<Grid> fetchScalarGrid(
  String url, {
  required double lo,
  required double hi,
  GridScale scale = GridScale.linear,
}) async {
  final bytes = await _fetchBytes(url);
  final img = await _decodeImage(bytes);
  final rgba = await _readRawRgba(img);
  final w = img.width;
  final h = img.height;
  final data = Float32List(w * h);
  if (scale == GridScale.log10) {
    final llo = math.log(lo) / math.ln10;
    final lhi = math.log(hi) / math.ln10;
    for (int i = 0; i < w * h; i++) {
      final px = rgba[i * 4];
      data[i] = px == 0
          ? double.nan
          : math.pow(10, llo + ((px - 1) / 254.0) * (lhi - llo)).toDouble();
    }
  } else {
    for (int i = 0; i < w * h; i++) {
      final px = rgba[i * 4];
      data[i] = px == 0 ? double.nan : lo + ((px - 1) / 254.0) * (hi - lo);
    }
  }
  img.dispose();
  return Grid(w, h, data);
}

class WaveGrid {
  final int width;
  final int height;
  final Float32List hs;
  final Float32List tp;
  final Float32List dp;
  const WaveGrid(this.width, this.height, this.hs, this.tp, this.dp);

  Grid get hsGrid => Grid(width, height, hs);
}

Future<WaveGrid> fetchWaveGrid(
  String url, {
  required double hLo,
  required double hHi,
  required double pLo,
  required double pHi,
}) async {
  final bytes = await _fetchBytes(url);
  final img = await _decodeImage(bytes);
  final rgba = await _readRawRgba(img);
  final w = img.width;
  final h = img.height;
  final hs = Float32List(w * h);
  final tp = Float32List(w * h);
  final dp = Float32List(w * h);
  for (int i = 0; i < w * h; i++) {
    final a = rgba[i * 4 + 3];
    if (a == 0) {
      hs[i] = double.nan;
      tp[i] = double.nan;
      dp[i] = double.nan;
    } else {
      hs[i] = hLo + (rgba[i * 4] / 255.0) * (hHi - hLo);
      tp[i] = pLo + (rgba[i * 4 + 1] / 255.0) * (pHi - pLo);
      dp[i] = (rgba[i * 4 + 2] / 255.0) * 360.0;
    }
  }
  img.dispose();
  return WaveGrid(w, h, hs, tp, dp);
}

class UVGrid {
  final int width;
  final int height;
  final Float32List u;
  final Float32List v;
  final Float32List speedKt;
  const UVGrid(this.width, this.height, this.u, this.v, this.speedKt);

  Grid get speedGrid => Grid(width, height, speedKt);
}

Future<UVGrid> fetchUVGrid(
  String url, {
  required double uvLo,
  required double uvHi,
}) async {
  final bytes = await _fetchBytes(url);
  final img = await _decodeImage(bytes);
  final rgba = await _readRawRgba(img);
  final w = img.width;
  final h = img.height;
  final u = Float32List(w * h);
  final v = Float32List(w * h);
  final speed = Float32List(w * h);
  final span = uvHi - uvLo;
  for (int i = 0; i < w * h; i++) {
    final a = rgba[i * 4 + 3];
    if (a == 0) {
      u[i] = double.nan;
      v[i] = double.nan;
      speed[i] = double.nan;
    } else {
      final uu = uvLo + (rgba[i * 4] / 255.0) * span;
      final vv = uvLo + (rgba[i * 4 + 1] / 255.0) * span;
      u[i] = uu;
      v[i] = vv;
      speed[i] = math.sqrt(uu * uu + vv * vv) * 1.94384;
    }
  }
  img.dispose();
  return UVGrid(w, h, u, v, speed);
}

// Suppress lint about unused import.
// ignore: unused_element
void _keepAssetImport() => rootBundle;
