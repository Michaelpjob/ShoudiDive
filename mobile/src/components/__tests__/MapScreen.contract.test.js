const fs = require("node:fs");
const path = require("node:path");

const MAP_SCREEN_PATH = path.join(__dirname, "..", "MapScreen.jsx");
const MAP_SCREEN_SOURCE = fs.readFileSync(MAP_SCREEN_PATH, "utf8");

describe("MapScreen source contracts", () => {
  it("does not reintroduce the old Skia pixel-pipeline", () => {
    expect(MAP_SCREEN_SOURCE).not.toMatch(/@shopify\/react-native-skia/);
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\breadPixels\b/);
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\bMakeImage\b/);
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\bcolorizeImage\b/);
  });

  it("does not fall back to screen-space region math that drifts from the native map", () => {
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\bonRegionChange\b/);
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\boverlayBox\b/);
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\bxPerLng\b/);
    expect(MAP_SCREEN_SOURCE).not.toMatch(/\byPerLat\b/);
  });

  it("still fits the camera to the known bbox instead of guessing from padding", () => {
    expect(MAP_SCREEN_SOURCE).toMatch(/fitToCoordinates\s*\(\s*BBOX_RING/);
    expect(MAP_SCREEN_SOURCE).toMatch(/INITIAL_EDGE_PADDING/);
  });

  it("keeps explicit async overlay state surfaced in the screen", () => {
    expect(MAP_SCREEN_SOURCE).toMatch(/overlayState/);
    expect(MAP_SCREEN_SOURCE).toMatch(/status:\s*"loading"/);
    expect(MAP_SCREEN_SOURCE).toMatch(/status:\s*"error"/);
  });
});
