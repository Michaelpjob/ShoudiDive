// Day-1 map screen. Renders Apple Maps (or Google on Android) zoomed
// to the model's bbox, with the active layer's heatmap PNG painted on
// top via the native <Overlay> primitive. Tap-to-pin coming next; for
// v1 we just want to see a real, performant map with our data on it.
import { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import MapView, { Marker, Overlay, PROVIDER_DEFAULT } from "react-native-maps";

import { BBOX, BBOX_REGION, SAVED_SPOTS } from "../lib/mapData.js";
import {
  loadManifest,
  subscribe,
  isReady,
  getLayerPngUrl,
  getGeneratedAt,
} from "../lib/dataSource.js";


// The five layer chips — same set + order as the web peek strip so
// the user's mental model carries over. v1 ships the static-PNG
// layers (sst, chl, viz). Wind + swell come in v2 once we wire the
// 5-day forecast feeds.
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

  useEffect(() => {
    loadManifest();
    return subscribe(() => setTick((t) => t + 1));
  }, []);

  const ready = isReady();
  const pngUrl = getLayerPngUrl(layer, composite);
  // viz slot is always the "now" composite; the 1/2/3 day buttons
  // only matter for sst + chl.
  const compositeButtonsActive = layer === "sst" || layer === "chl";

  return (
    <View style={styles.root}>
      <MapView
        style={styles.map}
        provider={PROVIDER_DEFAULT}
        initialRegion={BBOX_REGION}
        // Standard map type; satellite gets considered later but the
        // heatmap reads better against the default light terrain.
        mapType="standard"
        showsUserLocation
        showsCompass
        showsMyLocationButton
        pitchEnabled={false}
        rotateEnabled={false}
      >
        {/* The full-bbox PNG overlay. The Overlay component takes a
            bounds [[lat_max, lng_min], [lat_min, lng_max]] (NW, SE)
            and stretches the image to fit. Native zlib + WebP/PNG
            decoding happens off the JS thread. */}
        {pngUrl && (
          <Overlay
            image={{ uri: pngUrl }}
            bounds={[
              [BBOX.latMax, BBOX.lngMin], // top-left (NW)
              [BBOX.latMin, BBOX.lngMax], // bottom-right (SE)
            ]}
          />
        )}

        {/* Saved-spot pins, same set as web. Tap shows the spot name
            for now; tap-to-pin readout comes in the next iteration
            once we have a value-at-coord lookup. */}
        {SAVED_SPOTS.map((s) => (
          <Marker
            key={s.id}
            coordinate={{ latitude: s.lat, longitude: s.lng }}
            title={s.name}
            description={`${s.lat.toFixed(2)}°N ${Math.abs(s.lng).toFixed(2)}°W`}
          />
        ))}
      </MapView>

      {/* Status pill — minimal v1: layer label + window. Will grow into
          the full peek strip with tap-to-pin readout shortly. */}
      <View style={styles.statusPill} pointerEvents="none">
        <View style={styles.dot} />
        <Text style={styles.statusText}>
          {layerLabel(layer)} ·{" "}
          {compositeButtonsActive ? `${composite}-day` : "now"}
        </Text>
      </View>

      {/* Layer chip strip */}
      <View style={styles.chipStrip}>
        {LAYERS.map((L) => {
          const active = layer === L.id;
          return (
            <View
              key={L.id}
              style={[
                styles.chip,
                active && styles.chipActive,
                !L.ready && styles.chipDisabled,
              ]}
              onTouchEnd={() => L.ready && setLayer(L.id)}
            >
              <Text style={[styles.chipLabel, active && styles.chipLabelActive]}>
                {L.label}
              </Text>
              <Text style={[styles.chipSub, active && styles.chipSubActive]}>
                {L.ready ? L.unit : "soon"}
              </Text>
            </View>
          );
        })}
      </View>

      {/* Composite buttons (only for sst/chl). */}
      {compositeButtonsActive && (
        <View style={styles.compositeStrip}>
          {[1, 2, 3].map((d) => (
            <View
              key={d}
              style={[styles.compChip, composite === d && styles.compChipActive]}
              onTouchEnd={() => setComposite(d)}
            >
              <Text style={[styles.compText, composite === d && styles.compTextActive]}>
                {d}-day
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Loading indicator when manifest is in flight. */}
      {!ready && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="small" />
          <Text style={styles.loadingText}>Loading conditions…</Text>
        </View>
      )}
      {ready && !pngUrl && (
        <View style={styles.loadingOverlay}>
          <Text style={styles.loadingText}>
            No PNG for {layerLabel(layer)} {compositeButtonsActive ? `${composite}-day` : ""}
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
