// Unit tests for the colormap logic. We don't actually invoke Skia
// here (jest doesn't have a native Skia runtime); we test the pure-JS
// pieces — the value→RGB lookups + the LUT structure. The Skia
// MakeImage / readPixels parts are exercised by the runtime smoke
// test (Layer 6) and the visual tests (Layer 7).
//
// These are guardrails: if anyone changes a color ramp here without
// changing the matching ramp on the web frontend, the look diverges
// across platforms. Spotting that AFTER a deploy is much more
// painful than before.

// Mock the @shopify/react-native-skia import so colors.js can be
// imported in jest. We never actually call Skia methods in the
// tests below — the public functions we exercise (valueToRGB
// behaviour via colorizeImage's branches; LUT building via
// inspection) don't reach Skia.
jest.mock("@shopify/react-native-skia", () => ({
  Skia: {
    Data: { fromBytes: jest.fn() },
    Image: { MakeImage: jest.fn() },
  },
  AlphaType: { Unpremul: "unpremul" },
  ColorType: { RGBA_8888: "rgba8888" },
}));

const colors = require("../colors.js");


// Black-box: rebuild the LUT the same way the production code does,
// then test specific entries against known web ramp anchors. We
// can't import the LUT directly (it's module-private), so we
// exercise it via colorizeImage with a one-pixel image. But that
// still needs Skia.readPixels; instead, replicate the LUT here
// using the same range / stops constants. If they drift, the test
// catches it — that's the entire point.

// SST anchors copied verbatim from src/lib/colors.js. If you change
// one in the production module, you must change it here.
const SST_ANCHORS = [
  // [pixel byte, expected R, G, B]
  // px=0  → no data → transparent (alpha 0)
  { px: 0,    expected: [0, 0, 0, 0] },
  // px=1  → t≈0, deep blue (12, 38, 130)
  { px: 1,    expected: [12, 38, 130, 255] },
  // px=255 → t=1, deep red (170, 20, 35)
  { px: 255,  expected: [170, 20, 35, 255] },
];

const VIZ_ANCHORS = [
  // viz range is 0..80 ft, pixel 1 → ~0 ft
  { px: 1,    expected: [194, 65, 12, 255] },     // Poor — burnt orange
  // pixel ~32  → ~10 ft → Fair
  // pixel ~64  → ~20 ft → Good
  // pixel ~96  → ~30 ft → Very Good
  // pixel ~159 → ~50 ft → Excellent
  { px: 0,    expected: [0, 0, 0, 0] },
];


describe("color ramp LUTs", () => {
  // We inspect colorizeImage's call to Skia.Image.MakeImage to read
  // out the colorized buffer. Mock readPixels to return a synthetic
  // grayscale source whose pixels span the full 0..255 range.
  const Skia = require("@shopify/react-native-skia").Skia;

  it("SST: px=1 maps to deep blue, px=255 maps to deep red, px=0 stays transparent", () => {
    const grayPixels = new Uint8Array(256 * 4);
    for (let px = 0; px < 256; px++) {
      grayPixels[px * 4 + 0] = px;
      grayPixels[px * 4 + 1] = px;
      grayPixels[px * 4 + 2] = px;
      grayPixels[px * 4 + 3] = 255;
    }
    const fakeImage = {
      width: () => 256,
      height: () => 1,
      readPixels: () => grayPixels,
    };
    let captured;
    Skia.Image.MakeImage.mockImplementation((info, data, rowBytes) => {
      // Skia.Data.fromBytes wraps the Uint8Array; in the mock we
      // can ignore the wrapping and just look at the array passed
      // to fromBytes.
      captured = Skia.Data.fromBytes.mock.calls.at(-1)[0];
      return { mocked: true };
    });
    colors.colorizeImage(fakeImage, "sst");

    expect(captured).toBeInstanceOf(Uint8Array);
    expect(captured.length).toBe(grayPixels.length);

    for (const { px, expected } of SST_ANCHORS) {
      const o = px * 4;
      const got = [captured[o], captured[o + 1], captured[o + 2], captured[o + 3]];
      expect(got).toEqual(expected);
    }
  });

  it("VIZ: px=1 maps to burnt orange (Poor); px=0 stays transparent", () => {
    const grayPixels = new Uint8Array(256 * 4);
    for (let px = 0; px < 256; px++) {
      grayPixels[px * 4 + 0] = px;
      grayPixels[px * 4 + 1] = px;
      grayPixels[px * 4 + 2] = px;
      grayPixels[px * 4 + 3] = 255;
    }
    const fakeImage = {
      width: () => 256,
      height: () => 1,
      readPixels: () => grayPixels,
    };
    let captured;
    Skia.Image.MakeImage.mockImplementation(() => {
      captured = Skia.Data.fromBytes.mock.calls.at(-1)[0];
      return { mocked: true };
    });
    colors.colorizeImage(fakeImage, "viz");

    for (const { px, expected } of VIZ_ANCHORS) {
      const o = px * 4;
      const got = [captured[o], captured[o + 1], captured[o + 2], captured[o + 3]];
      expect(got).toEqual(expected);
    }
  });

  it("CHL: px=0 transparent; px=255 ends up green-ish (productive water side of ramp)", () => {
    const grayPixels = new Uint8Array(256 * 4);
    for (let px = 0; px < 256; px++) {
      grayPixels[px * 4 + 0] = px;
      grayPixels[px * 4 + 1] = px;
      grayPixels[px * 4 + 2] = px;
      grayPixels[px * 4 + 3] = 255;
    }
    const fakeImage = {
      width: () => 256,
      height: () => 1,
      readPixels: () => grayPixels,
    };
    let captured;
    Skia.Image.MakeImage.mockImplementation(() => {
      captured = Skia.Data.fromBytes.mock.calls.at(-1)[0];
      return { mocked: true };
    });
    colors.colorizeImage(fakeImage, "chl");

    // px=0 transparent
    expect([captured[0], captured[1], captured[2], captured[3]]).toEqual([0, 0, 0, 0]);
    // px=1 (chl ~0.05 mg/m³, gin clear) is the FIRST chl stop = deep blue
    const r1 = captured[1 * 4], g1 = captured[1 * 4 + 1], b1 = captured[1 * 4 + 2];
    expect(r1).toBe(10);  expect(g1).toBe(50);  expect(b1).toBe(140);
    // px=255 (chl ~20 mg/m³, dense bloom) is the LAST chl stop = dense green
    const r255 = captured[255 * 4], g255 = captured[255 * 4 + 1], b255 = captured[255 * 4 + 2];
    expect(r255).toBe(50);  expect(g255).toBe(130);  expect(b255).toBe(40);
  });

  it("colorizeImage returns null for null input image", () => {
    expect(colors.colorizeImage(null, "sst")).toBeNull();
  });

  it("colorizeImage returns null for zero-sized image", () => {
    const fakeImage = {
      width: () => 0,
      height: () => 0,
      readPixels: () => null,
    };
    expect(colors.colorizeImage(fakeImage, "sst")).toBeNull();
  });
});


describe("color ramp ranges", () => {
  it("SST_RANGE matches the pipeline encoder (9..25 °C)", () => {
    expect(colors.SST_RANGE).toEqual([9, 25]);
  });

  it("VIZ_RANGE_FT matches the pipeline encoder (0..80 ft)", () => {
    expect(colors.VIZ_RANGE_FT).toEqual([0, 80]);
  });
});
