import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import MapView, { Marker, Overlay, PROVIDER_DEFAULT } from "react-native-maps";

import {
  BBOX_BOUNDS,
  BBOX_REGION,
  BBOX_RING,
  SAVED_SPOTS,
} from "../lib/mapData.js";
import {
  loadManifest,
  subscribe,
  isReady,
  getLayerPngUrl,
} from "../lib/dataSource.js";

const LAYERS = [
  { id: "sst", label: "Temp", unit: "degF", ready: true },
  { id: "chl", label: "Chl", unit: "mg/m^3", ready: true },
  { id: "wind", label: "Wind", unit: "kt", ready: false },
  { id: "swell", label: "Swell", unit: "ft", ready: false },
  { id: "viz", label: "Vis", unit: "ft", ready: true },
];

const INITIAL_EDGE_PADDING = { top: 16, right: 16, bottom: 16, left: 16 };
const OVERLAY_OPACITY = 0.72;

export default function MapScreen() {
  const mapRef = useRef(null);
  const didInitialFitRef = useRef(false);

  const [layer, setLayer] = useState("sst");
  const [composite, setComposite] = useState(2);
  const [, setTick] = useState(0);
  const [mapReady, setMapReady] = useState(false);
  const [mapLayout, setMapLayout] = useState({ width: 0, height: 0 });
  const [overlayState, setOverlayState] = useState({
    status: "idle",
    error: null,
  });

  useEffect(() => {
    loadManifest();
    return subscribe(() => setTick((t) => t + 1));
  }, []);

  const ready = isReady();
  const pngUrl = getLayerPngUrl(layer, composite);
  const compositeButtonsActive = layer === "sst" || layer === "chl";

  useEffect(() => {
    if (!pngUrl) {
      setOverlayState({ status: "idle", error: null });
      return undefined;
    }

    let cancelled = false;
    setOverlayState({ status: "loading", error: null });

    Image.prefetch(pngUrl)
      .then((ok) => {
        if (cancelled) return;
        if (!ok) {
          setOverlayState({
            status: "error",
            error: "image prefetch returned false",
          });
          return;
        }
        setOverlayState({ status: "ready", error: null });
      })
      .catch((error) => {
        if (cancelled) return;
        setOverlayState({
          status: "error",
          error: String(error?.message || error || "image prefetch failed"),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [pngUrl]);

  useEffect(() => {
    if (!mapReady || !mapLayout.width || !mapLayout.height || didInitialFitRef.current) {
      return undefined;
    }

    didInitialFitRef.current = true;
    const timer = setTimeout(() => {
      mapRef.current?.fitToCoordinates(BBOX_RING, {
        edgePadding: INITIAL_EDGE_PADDING,
        animated: false,
      });
    }, 0);

    return () => clearTimeout(timer);
  }, [mapReady, mapLayout.height, mapLayout.width]);

  return (
    <View style={styles.root}>
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={PROVIDER_DEFAULT}
        initialRegion={BBOX_REGION}
        mapType="standard"
        showsUserLocation
        showsCompass
        showsMyLocationButton
        pitchEnabled={false}
        rotateEnabled={false}
        onMapReady={() => setMapReady(true)}
        onLayout={(event) => {
          const { width, height } = event.nativeEvent.layout;
          setMapLayout({ width, height });
        }}
      >
        {pngUrl && overlayState.status === "ready" && (
          <Overlay
            key={pngUrl}
            bounds={BBOX_BOUNDS}
            image={{ uri: pngUrl }}
            opacity={OVERLAY_OPACITY}
            zIndex={2}
          />
        )}

        {SAVED_SPOTS.map((spot) => (
          <Marker
            key={spot.id}
            coordinate={{ latitude: spot.lat, longitude: spot.lng }}
            title={spot.name}
            description={`${spot.lat.toFixed(2)}N ${Math.abs(spot.lng).toFixed(2)}W`}
            zIndex={10}
            pinColor="red"
          />
        ))}
      </MapView>

      <View style={styles.statusPill} pointerEvents="none">
        <View
          style={[
            styles.dot,
            !pngUrl && styles.dotIdle,
            overlayState.status === "loading" && styles.dotLoading,
            overlayState.status === "error" && styles.dotError,
          ]}
        />
        <Text style={styles.statusText}>
          {layerLabel(layer)} . {compositeButtonsActive ? `${composite}-day` : "now"}
          {overlayState.status === "loading" ? " . loading" : ""}
          {overlayState.status === "error" ? " . error" : ""}
        </Text>
      </View>

      {__DEV__ && (
        <View style={styles.debugPill} pointerEvents="none">
          <Text style={styles.debugText} numberOfLines={3}>
            {`overlay:${overlayState.status} img:${overlayAssetName(pngUrl) || "none"} fit:${mapReady ? "ready" : "pending"} layout:${mapLayout.width || 0}x${mapLayout.height || 0}${overlayState.error ? ` err:${overlayState.error}` : ""}`}
          </Text>
        </View>
      )}

      <View style={styles.chipStrip}>
        {LAYERS.map((entry) => {
          const active = layer === entry.id;
          return (
            <Pressable
              key={entry.id}
              disabled={!entry.ready}
              onPress={() => setLayer(entry.id)}
              style={({ pressed }) => [
                styles.chip,
                active && styles.chipActive,
                !entry.ready && styles.chipDisabled,
                pressed && entry.ready && styles.chipPressed,
              ]}
            >
              <Text style={[styles.chipLabel, active && styles.chipLabelActive]}>
                {entry.label}
              </Text>
              <Text style={[styles.chipSub, active && styles.chipSubActive]}>
                {entry.ready ? entry.unit : "soon"}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {compositeButtonsActive && (
        <View style={styles.compositeStrip}>
          {[1, 2, 3].map((days) => (
            <Pressable
              key={days}
              onPress={() => setComposite(days)}
              style={({ pressed }) => [
                styles.compChip,
                composite === days && styles.compChipActive,
                pressed && styles.compChipPressed,
              ]}
            >
              <Text
                style={[
                  styles.compText,
                  composite === days && styles.compTextActive,
                ]}
              >
                {days}-day
              </Text>
            </Pressable>
          ))}
        </View>
      )}

      {!ready && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="small" />
          <Text style={styles.loadingText}>Loading conditions...</Text>
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

function overlayAssetName(url) {
  if (!url) return null;
  return url.split("?")[0].split("/").pop() || url;
}

function layerLabel(layer) {
  switch (layer) {
    case "sst":
      return "Sea Temp";
    case "chl":
      return "Chlorophyll";
    case "wind":
      return "Wind";
    case "swell":
      return "Swell";
    case "viz":
      return "Visibility";
    default:
      return layer;
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
  dotLoading: { backgroundColor: "rgb(234, 179, 8)" },
  dotError: { backgroundColor: "rgb(220, 38, 38)" },
  dotIdle: { backgroundColor: "rgb(148, 163, 184)" },
  statusText: { fontSize: 12, fontWeight: "600", color: "#0f172a" },

  debugPill: {
    position: "absolute",
    top: 102,
    left: 12,
    right: 12,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    backgroundColor: "rgba(15,23,42,0.82)",
  },
  debugText: {
    fontSize: 10,
    lineHeight: 13,
    color: "#e2e8f0",
  },

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
