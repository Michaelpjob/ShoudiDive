import { BBOX, BBOX_CENTER, BBOX_REGION, BBOX_RING, SAVED_SPOTS } from "../mapData.js";


// These are guardrails for the constants the rest of the app depends
// on. None of this should change without a deliberate decision —
// tests fail loudly if they do, surfacing the "did you mean to widen
// the bbox?" question before a deploy.

describe("BBOX", () => {
  it("covers the CA coast from Bodega Bay south to Coronado Islands", () => {
    expect(BBOX.latMin).toBeLessThan(33);   // includes Coronados (32.42°N)
    expect(BBOX.latMax).toBeGreaterThan(36); // includes Monterey (36.62°N)
    expect(BBOX.lngMin).toBeLessThan(-122);  // far enough west for offshore banks
    expect(BBOX.lngMax).toBeGreaterThan(-118); // east of San Diego coast
  });

  it("matches the model pipeline's bbox so PNG overlays align", () => {
    // If these change on the mobile side without the pipeline
    // changing in lockstep, the heatmap PNG won't fit the bbox.
    expect(BBOX.latMin).toBe(31.8);
    expect(BBOX.latMax).toBe(37.6);
    expect(BBOX.lngMin).toBe(-124.0);
    expect(BBOX.lngMax).toBe(-116.8);
  });
});

describe("BBOX_CENTER", () => {
  it("sits at the geometric centroid", () => {
    expect(BBOX_CENTER.latitude).toBeCloseTo(34.7, 1);
    expect(BBOX_CENTER.longitude).toBeCloseTo(-120.4, 1);
  });
});

describe("BBOX_REGION", () => {
  it("pads beyond the raw bbox so the user sees ocean on either side", () => {
    expect(BBOX_REGION.latitudeDelta).toBeGreaterThan(BBOX.latMax - BBOX.latMin);
    expect(BBOX_REGION.longitudeDelta).toBeGreaterThan(BBOX.lngMax - BBOX.lngMin);
  });
});

describe("BBOX_RING", () => {
  it("walks the corners NW → NE → SE → SW (clockwise from top-left)", () => {
    expect(BBOX_RING).toHaveLength(4);
    const [nw, ne, se, sw] = BBOX_RING;
    expect(nw).toEqual({ latitude: BBOX.latMax, longitude: BBOX.lngMin });
    expect(ne).toEqual({ latitude: BBOX.latMax, longitude: BBOX.lngMax });
    expect(se).toEqual({ latitude: BBOX.latMin, longitude: BBOX.lngMax });
    expect(sw).toEqual({ latitude: BBOX.latMin, longitude: BBOX.lngMin });
  });
});

describe("SAVED_SPOTS", () => {
  it("has 8 diver-curated CA spots", () => {
    expect(SAVED_SPOTS).toHaveLength(8);
  });

  it("every spot has a non-empty id, name, and a coordinate inside the bbox", () => {
    for (const s of SAVED_SPOTS) {
      expect(typeof s.id).toBe("string");
      expect(s.id.length).toBeGreaterThan(0);
      expect(typeof s.name).toBe("string");
      expect(s.name.length).toBeGreaterThan(0);
      expect(typeof s.lat).toBe("number");
      expect(typeof s.lng).toBe("number");
      expect(s.lat).toBeGreaterThanOrEqual(BBOX.latMin);
      expect(s.lat).toBeLessThanOrEqual(BBOX.latMax);
      expect(s.lng).toBeGreaterThanOrEqual(BBOX.lngMin);
      expect(s.lng).toBeLessThanOrEqual(BBOX.lngMax);
    }
  });

  it("ids are unique (Marker key collision would silently drop pins)", () => {
    const ids = SAVED_SPOTS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("includes the canonical SoCal kelp + Channel Islands spots", () => {
    const ids = SAVED_SPOTS.map((s) => s.id);
    expect(ids).toEqual(
      expect.arrayContaining([
        "lajolla",
        "catalina",
        "coronados",
        "santacruzi",
        "ptconception",
      ])
    );
  });
});
