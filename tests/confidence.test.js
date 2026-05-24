// Tests for src/lib/confidence.js. Pins the static scoring matrix
// (every region/layer pair has a defined entry, no surprise drops to
// CLIMATOLOGY) and the dynamic modulation behaviour (low chl coverage
// or stale generated_at correctly drop the score).
//
// Doesn't run in jsdom so we mock the two dependencies: activeRegion
// (returns the test's region) and getDataState (returns a controlled
// manifest snapshot).

import { jest } from "@jest/globals";

jest.unstable_mockModule("../src/lib/region.js", () => ({
  activeRegion: jest.fn(() => "ca"),
  dataPath:     jest.fn((p) => p),
  manifestUrl:  jest.fn(() => "/data/manifest.json"),
  rewriteManifestUrls: jest.fn((m) => m),
  listRegions:  jest.fn(() => ["ca", "pnw", "tropical", "baja"]),
  isProductionHost: jest.fn(() => false),
  validRegionsForHost: jest.fn(() => ["ca", "pnw", "tropical", "baja"]),
  DEFAULT_REGION_ID: "ca",
}));

jest.unstable_mockModule("../src/lib/dataSource.js", () => ({
  getDataState: jest.fn(() => ({ manifest: null })),
}));

const { activeRegion } = await import("../src/lib/region.js");
const { getDataState } = await import("../src/lib/dataSource.js");
const { getLayerConfidence, getRegionConfidence } = await import("../src/lib/confidence.js");

describe("getLayerConfidence", () => {
  beforeEach(() => {
    activeRegion.mockReturnValue("ca");
    getDataState.mockReturnValue({ manifest: null });
  });

  test("every region defines every layer (no surprise nulls)", () => {
    const layers = ["sst", "chl", "wind", "swell", "current", "viz"];
    for (const region of ["ca", "pnw", "tropical", "baja"]) {
      activeRegion.mockReturnValue(region);
      for (const layer of layers) {
        const c = getLayerConfidence(layer);
        expect(c).not.toBeNull();
        expect(c.score).toBeGreaterThanOrEqual(1);
        expect(c.score).toBeLessThanOrEqual(5);
        expect(typeof c.label).toBe("string");
        expect(typeof c.source).toBe("string");
      }
    }
  });

  test("Baja currents score is the documented 2/5 (no HFRNet)", () => {
    activeRegion.mockReturnValue("baja");
    const c = getLayerConfidence("current");
    expect(c.score).toBe(2);
    expect(c.label).toBe("Inferred");
    expect(c.source).toMatch(/Tide \+ Ekman/);
  });

  test("CA currents score is 4/5 (HFRNet observed)", () => {
    activeRegion.mockReturnValue("ca");
    const c = getLayerConfidence("current");
    expect(c.score).toBe(4);
    expect(c.source).toMatch(/HFRNet/);
  });

  test("chl coverage_frac < 0.4 drops the score by 1", () => {
    activeRegion.mockReturnValue("ca");
    getDataState.mockReturnValue({
      manifest: { layers: { chl: { windows: { "1d": { coverage_frac: 0.25, mean_age_days: 1.0 } } } } },
    });
    const c = getLayerConfidence("chl");
    expect(c.score).toBe(3);  // ceiling 4 → 3
    expect(c.modReasons.some((r) => r.includes("coverage"))).toBe(true);
  });

  test("chl mean_age_days > 5 drops the score by 1", () => {
    activeRegion.mockReturnValue("ca");
    getDataState.mockReturnValue({
      manifest: { layers: { chl: { windows: { "1d": { coverage_frac: 0.8, mean_age_days: 7.0 } } } } },
    });
    const c = getLayerConfidence("chl");
    expect(c.score).toBe(3);  // ceiling 4 → 3
    expect(c.modReasons.some((r) => r.includes("days old"))).toBe(true);
  });

  test("score floor is 1 — never returns 0 or negative", () => {
    activeRegion.mockReturnValue("tropical");
    getDataState.mockReturnValue({
      manifest: { layers: { current: { generated_at: "2020-01-01T00:00:00Z" } } },
    });
    const c = getLayerConfidence("current");
    expect(c.score).toBeGreaterThanOrEqual(1);
  });

  test("unknown region returns null (graceful)", () => {
    activeRegion.mockReturnValue("atlantic");  // not in matrix
    expect(getLayerConfidence("sst")).toBeNull();
  });
});

describe("getRegionConfidence", () => {
  beforeEach(() => {
    getDataState.mockReturnValue({ manifest: null });
  });

  test("returns the WEAKEST layer score (honest about gaps)", () => {
    activeRegion.mockReturnValue("baja");
    const r = getRegionConfidence();
    expect(r.score).toBe(2);  // currents is 2/5
    expect(r.weakestLayer).toBe("current");
  });

  test("CA is bottlenecked by chl/swell/current/viz at 4 (no 5s in those rows)", () => {
    activeRegion.mockReturnValue("ca");
    const r = getRegionConfidence();
    expect(r.score).toBe(4);
  });
});
