// Web fallback for the map screen. react-native-maps is iOS/Android
// only (it imports codegenNativeCommands which Metro can't bundle for
// web). The web version exists for ONE reason: so the dev (Claude in
// agent mode) can render the app in a browser via Claude Preview to
// validate layout / state / chip behaviour without touching a real
// device.
//
// Color rendering is INTENTIONALLY skipped on web. Why:
//   - Skia on web requires CanvasKit (WASM) be loaded explicitly via
//     LoadSkiaWeb before any Skia primitive is touched. Adding that
//     setup creates a real source of bugs (load timing, WASM URL
//     resolution) for a target that's only there to validate layout.
//   - Color correctness is already proven at the unit-test layer —
//     `src/lib/__tests__/colors.test.js` exercises the LUT against
//     known anchors (px=1 → deep blue for SST, px=255 → deep red,
//     etc). If the LUT is right and Skia.MakeImage is called with
//     the right buffer (which is mockable in jest), the rendered
//     output IS right. Native side proves the integration; visual
//     tests on web prove the layout.
//
// So: web shows the raw grayscale PNG. Visual tests on web verify
// LAYOUT + STATE. Native shows colourised heatmap via Skia.

import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { SAVED_SPOTS } from "../lib/mapData.js";
import {
  loadManifest,
  subscribe,
  isReady,
  getLayerPngUrl,
} from "../lib/dataSource.js";


const LAYERS = [
  { id: "sst",  label: "Temp",  unit: "°F",     ready: true },
  { id: "chl",  label: "Chl",   unit: "mg/m³",  ready: true },
  { id: "wind", label: "Wind",  unit: "kt",     ready: false },
  { id: "swell",label: "Swell", unit: "ft",     ready: false },
  { id: "viz",  label: "Vis",   unit: "ft",     ready: true },
];


export default function MapScreen() {
  const [layer, setLayer] = useState("sst");
  const [composite, setComposite] = useState(2);
  const [, setTick] = useState(0);

  useEffect(() => {
    loadManifest();
    return subscribe(() => setTick((t) => t + 1));
  }, []);

  const ready = isReady();
  const pngUrl = getLayerPngUrl(layer, composite);
  const compositeButtonsActive = layer === "sst" || layer === "chl";

  return (
    <View style={styles.root}>
      <View style={styles.fakeMap}>
        <View style={styles.webBanner} pointerEvents="none">
          <Text style={styles.webBannerText}>
            Web preview · iOS / Android show a real native map here
          </Text>
        </View>
        {pngUrl && (
          <Image
            source={{ uri: pngUrl }}
            style={styles.fakeMapImage}
            resizeMode="cover"
          />
        )}
      </View>

      <View style={styles.statusPill} pointerEvents="none">
        <View
          style={[
            styles.dot,
            !pngUrl && styles.dotIdle,
          ]}
        />
        <Text style={styles.statusText}>
          {layerLabel(layer)} ·{" "}
          {compositeButtonsActive ? `${composite}-day` : "now"}
        </Text>
      </View>

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

      <View style={styles.spotsBadge} pointerEvents="none">
        <Text style={styles.spotsBadgeText}>{SAVED_SPOTS.length} saved spots</Text>
      </View>
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
  fakeMap: {
    flex: 1,
    backgroundColor: "#dbeafe",
    overflow: "hidden",
  },
  fakeMapImage: { flex: 1, opacity: 0.85 },

  webBanner: {
    position: "absolute",
    top: 12,
    left: "50%",
    transform: [{ translateX: -130 }],
    width: 260,
    paddingVertical: 4,
    paddingHorizontal: 10,
    borderRadius: 6,
    backgroundColor: "rgba(15, 23, 42, 0.85)",
    zIndex: 5,
  },
  webBannerText: {
    color: "#ffffff",
    fontSize: 10.5,
    textAlign: "center",
  },

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
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "rgb(34, 197, 94)",
  },
  dotIdle: { backgroundColor: "rgb(148, 163, 184)" },
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

  spotsBadge: {
    position: "absolute",
    bottom: 110,
    right: 12,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    backgroundColor: "rgba(15, 23, 42, 0.85)",
  },
  spotsBadgeText: { fontSize: 10, color: "#ffffff" },
});
