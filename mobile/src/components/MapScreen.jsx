// Day-1 map screen. Renders Apple Maps zoomed to the model's bbox,
// with the active layer's heatmap PNG painted on top via a Skia
// overlay synced to the map camera. We use Skia (not the built-in
// react-native-maps <Overlay>) because Overlay with remote URIs is
// flaky on Apple Maps — onLoad never fires and the image never
// renders. Skia gives us GPU-accelerated drawing and full control
// over coordinate transforms.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import MapView, { Marker, PROVIDER_DEFAULT } from "react-native-maps";
import { Canvas, Image as SkiaImage, useImage } from "@shopify/react-native-skia";

import { BBOX, BBOX_REGION, SAVED_SPOTS } from "../lib/mapData.js";
import {
  loadManifest,
  subscribe,
  isReady,
  getLayerPngUrl,
} from "../lib/dataSource.js";


// The five layer chips — same set + order as the web peek strip.
// v1 ships the static-PNG layers (sst, chl, viz). Wind + swell come
// in v2 once we wire the 5-day forecast feeds.
const LAYERS = [
  { id: "sst",  label: "Temp",  unit: "°F",     ready: true },
  { id: "chl",  label: "Chl",   unit: "mg/m³",  ready: true },
  { id: "wind", label: "Wind",  unit: "kt",     ready: false },
  { id: "swell",label: "Swell", unit: "ft",     ready: false },
  { id: "viz",  label: "Vis",   unit: "ft",     ready: true },
];


export default function MapScreen() {
  const [layer, setLayer] = useState("sst");
  const [composite, setComposite] = useState(2); // 2-day window (best balance)
  // Re-render trigger when the manifest lands or refreshes.
  const [, setTick] = useState(0);
  // Track the map's current region + measured size so we can compute
  // screen coordinates for the bbox corners (where the heatmap PNG
  // gets drawn). onRegionChange fires continuously during pan so the
  // overlay tracks smoothly.
  const [region, setRegion] = useState(BBOX_REGION);
  const [mapSize, setMapSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    loadManifest();
    return subscribe(() => setTick((t) => t + 1));
  }, []);

  const ready = isReady();
  const pngUrl = getLayerPngUrl(layer, composite);
  const compositeButtonsActive = layer === "sst" || layer === "chl";

  // Skia loads + decodes the PNG natively. `useImage` returns null
  // while loading, then the SkImage handle when ready. Re-runs when
  // pngUrl changes (different layer / composite).
  const skiaImage = useImage(pngUrl);

  // Compute the screen rectangle the heatmap should occupy by
  // mapping the bbox corners into pixel space using the current
  // region. Equirectangular math — accurate enough across our
  // 5°×7° bbox; Mercator distortion at lat 35°N is single-digit %
  // which won't be visible at this scale.
  const overlayBox = useMemo(() => {
    const { w, h } = mapSize;
    if (!w || !h) return null;
    const lngWest  = region.longitude - region.longitudeDelta / 2;
    const latNorth = region.latitude  + region.latitudeDelta  / 2;
    const xPerLng = w / region.longitudeDelta;
    const yPerLat = h / region.latitudeDelta;
    const x0 = (BBOX.lngMin - lngWest)  * xPerLng;
    const y0 = (latNorth - BBOX.latMax) * yPerLat;
    const x1 = (BBOX.lngMax - lngWest)  * xPerLng;
    const y1 = (latNorth - BBOX.latMin) * yPerLat;
    return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
  }, [region, mapSize]);

  return (
    <View style={styles.root}>
      <MapView
        style={styles.map}
        provider={PROVIDER_DEFAULT}
        initialRegion={BBOX_REGION}
        mapType="standard"
        showsUserLocation
        showsCompass
        showsMyLocationButton
        pitchEnabled={false}
        rotateEnabled={false}
        onRegionChange={setRegion}
        onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout;
          setMapSize({ w: width, h: height });
        }}
      >
        {/* Saved-spot pins. zIndex pushes them above the heatmap so a
            busy bbox doesn't bury them. Default callout shows the
            spot name; tap-to-pin readout comes in the next iteration. */}
        {SAVED_SPOTS.map((s) => (
          <Marker
            key={s.id}
            coordinate={{ latitude: s.lat, longitude: s.lng }}
            title={s.name}
            description={`${s.lat.toFixed(2)}°N ${Math.abs(s.lng).toFixed(2)}°W`}
            zIndex={10}
            pinColor="red"
          />
        ))}
      </MapView>

      {/* Skia overlay — sits absolute-positioned over the MapView,
          draws the layer PNG at the computed bbox screen coordinates.
          Updates every frame as the user pans/zooms. pointerEvents:
          none lets touch events pass through to the MapView so pan
          + tap on markers still work. */}
      {skiaImage && overlayBox && (
        <Canvas style={[StyleSheet.absoluteFill, styles.canvas]} pointerEvents="none">
          <SkiaImage
            image={skiaImage}
            x={overlayBox.x}
            y={overlayBox.y}
            width={overlayBox.width}
            height={overlayBox.height}
            opacity={0.7}
            fit="fill"
          />
        </Canvas>
      )}

      {/* Status pill — minimal v1: layer label, window, overlay
          load state. */}
      <View style={styles.statusPill} pointerEvents="none">
        <View
          style={[
            styles.dot,
            !pngUrl && styles.dotIdle,
            pngUrl && !skiaImage && styles.dotLoading,
          ]}
        />
        <Text style={styles.statusText}>
          {layerLabel(layer)} ·{" "}
          {compositeButtonsActive ? `${composite}-day` : "now"}
          {pngUrl && !skiaImage ? "  • loading" : ""}
        </Text>
      </View>

      {/* Layer chip strip — Pressable so taps fire on native. */}
      <View style={styles.chipStrip}>
        {LAYERS.map((L) => {
          const active = layer === L.id;
          return (
            <Pressable
              key={L.id}
              disabled={!L.ready}
              onPress={() => setLayer(L.id)}
              style={({ pressed }) => [
                styles.chip,
                active && styles.chipActive,
                !L.ready && styles.chipDisabled,
                pressed && L.ready && styles.chipPressed,
              ]}
            >
              <Text style={[styles.chipLabel, active && styles.chipLabelActive]}>
                {L.label}
              </Text>
              <Text style={[styles.chipSub, active && styles.chipSubActive]}>
                {L.ready ? L.unit : "soon"}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {/* Composite buttons (only for sst/chl). */}
      {compositeButtonsActive && (
        <View style={styles.compositeStrip}>
          {[1, 2, 3].map((d) => (
            <Pressable
              key={d}
              onPress={() => setComposite(d)}
              style={({ pressed }) => [
                styles.compChip,
                composite === d && styles.compChipActive,
                pressed && styles.compChipPressed,
              ]}
            >
              <Text
                style={[
                  styles.compText,
                  composite === d && styles.compTextActive,
                ]}
              >
                {d}-day
              </Text>
            </Pressable>
          ))}
        </View>
      )}

      {/* Loading indicator when the manifest is in flight. */}
      {!ready && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="small" />
          <Text style={styles.loadingText}>Loading conditions…</Text>
        </View>
      )}
      {ready && !pngUrl && (
        <View style={styles.loadingOverlay}>
          <Text style={styles.loadingText}>
            No PNG for {layerLabel(layer)}
            {compositeButtonsActive ? ` ${composite}-day` : ""}
          </Text>
        </View>
      )}
    </View>
  );
}


function layerLabel(layer) {
  switch (layer) {
    case "sst":   return "Sea Temp";
    case "chl":   return "Chlorophyll";
    case "wind":  return "Wind";
    case "swell": return "Swell";
    case "viz":   return "Visibility";
    default:      return layer;
  }
}


const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0369a1" },
  map: { flex: 1 },
  canvas: { backgroundColor: "transparent" },

  statusPill: {
    position: "absolute",
    top: 60,
    left: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.92)",
    shadowColor: "#000",
    shadowOpacity: 0.12,
    shadowOffset: { width: 0, height: 1 },
    shadowRadius: 4,
    elevation: 2,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "rgb(34, 197, 94)",
  },
  dotLoading: { backgroundColor: "rgb(234, 179, 8)" },
  dotIdle:    { backgroundColor: "rgb(148, 163, 184)" },
  statusText: { fontSize: 12, fontWeight: "600", color: "#0f172a" },

  chipStrip: {
    position: "absolute",
    bottom: 28,
    left: 8,
    right: 8,
    flexDirection: "row",
    gap: 4,
    backgroundColor: "rgba(255,255,255,0.96)",
    borderRadius: 14,
    padding: 4,
    shadowColor: "#000",
    shadowOpacity: 0.15,
    shadowOffset: { width: 0, height: 2 },
    shadowRadius: 8,
    elevation: 4,
  },
  chip: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 9,
    paddingHorizontal: 4,
    borderRadius: 10,
    backgroundColor: "transparent",
  },
  chipActive: { backgroundColor: "#0f172a" },
  chipPressed: { backgroundColor: "rgba(15,23,42,0.10)" },
  chipDisabled: { opacity: 0.45 },
  chipLabel: { fontSize: 13, fontWeight: "600", color: "#0f172a" },
  chipLabelActive: { color: "#ffffff" },
  chipSub: { fontSize: 10, color: "#64748b", marginTop: 1 },
  chipSubActive: { color: "rgba(255,255,255,0.85)" },

  compositeStrip: {
    position: "absolute",
    top: 60,
    right: 12,
    flexDirection: "row",
    gap: 4,
    backgroundColor: "rgba(255,255,255,0.92)",
    borderRadius: 10,
    padding: 3,
    shadowColor: "#000",
    shadowOpacity: 0.12,
    shadowOffset: { width: 0, height: 1 },
    shadowRadius: 4,
    elevation: 2,
  },
  compChip: {
    paddingVertical: 5,
    paddingHorizontal: 9,
    borderRadius: 7,
  },
  compChipActive: { backgroundColor: "#0f172a" },
  compChipPressed: { backgroundColor: "rgba(15,23,42,0.10)" },
  compText: { fontSize: 11, fontWeight: "600", color: "#334155" },
  compTextActive: { color: "#ffffff" },

  loadingOverlay: {
    position: "absolute",
    top: 110,
    alignSelf: "center",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: "rgba(255,255,255,0.95)",
  },
  loadingText: { fontSize: 12, color: "#0f172a" },
});
