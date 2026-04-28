// Geo constants ported from web (src/lib/mapData.js).
//
// Everything DOM-related (project / unproject / coastline geometry) is
// dropped — react-native-maps owns those operations natively. We only
// keep the data-shape constants the rest of the mobile app needs:
//
//   * BBOX  — the model's bounding box, used to position image overlays
//             and to clamp the camera.
//   * SAVED_SPOTS — the diver-curated list of named locations.
//
// Color ramps live in colors.js; visibility category cutoffs live in
// the screen that renders the legend.

export const BBOX = {
  latMin: 31.8,
  latMax: 37.6,
  lngMin: -124.0,
  lngMax: -116.8,
};

// Centroid of the bbox — handy as the initial map camera target.
export const BBOX_CENTER = {
  latitude:  (BBOX.latMin + BBOX.latMax) / 2,
  longitude: (BBOX.lngMin + BBOX.lngMax) / 2,
};

// Camera deltas for the initial fit. Keep these matched to the model
// bbox itself; view chrome padding belongs in the screen's fit logic,
// not baked into the geographic extent.
export const BBOX_REGION = {
  ...BBOX_CENTER,
  latitudeDelta:  BBOX.latMax - BBOX.latMin,
  longitudeDelta: BBOX.lngMax - BBOX.lngMin,
};

// Same diver-curated saved spots as the web app. Kept verbatim so
// validation lookups + spot-pin labels stay consistent across surfaces.
export const SAVED_SPOTS = [
  { id: "monterey",     name: "Monterey",      lat: 36.620, lng: -121.900 },
  { id: "morrobay",     name: "Morro Bay",     lat: 35.366, lng: -120.853 },
  { id: "ptconception", name: "Pt. Conception", lat: 34.450, lng: -120.470 },
  { id: "santacruzi",   name: "Santa Cruz I.",  lat: 34.000, lng: -119.740 },
  { id: "malibu",       name: "Malibu",         lat: 34.029, lng: -118.690 },
  { id: "catalina",     name: "Catalina",       lat: 33.388, lng: -118.420 },
  { id: "lajolla",      name: "La Jolla",       lat: 32.852, lng: -117.272 },
  { id: "coronados",    name: "Coronados",      lat: 32.420, lng: -117.270 },
];

// Bbox as a polygon ring — reused when a layer's PNG overlay needs to
// be placed via Polygon corner coordinates rather than a Rectangle.
// Order: NW, NE, SE, SW (clockwise from top-left).
export const BBOX_RING = [
  { latitude: BBOX.latMax, longitude: BBOX.lngMin },
  { latitude: BBOX.latMax, longitude: BBOX.lngMax },
  { latitude: BBOX.latMin, longitude: BBOX.lngMax },
  { latitude: BBOX.latMin, longitude: BBOX.lngMin },
];

// `react-native-maps` image overlays want southwest + northeast bounds,
// not the clockwise corner ring above.
export const BBOX_BOUNDS = [
  [BBOX.latMin, BBOX.lngMin],
  [BBOX.latMax, BBOX.lngMax],
];
