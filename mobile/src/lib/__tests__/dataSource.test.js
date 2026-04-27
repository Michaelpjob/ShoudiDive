// Tests for the manifest fetcher + URL resolver. These run in
// pure Node (jsdom-free) — the data layer has zero RN / DOM
// dependencies on purpose, so tests are fast and reliable.
//
// Each test resets the module to clear the in-module cache (manifest,
// inflight, listeners). Without resetModules the cached manifest from
// test 1 leaks into test 2.

const SAMPLE_MANIFEST = {
  generated_at: "2026-04-27T12:00:00Z",
  layers: {
    sst: {
      windows: {
        "1d": { url: "/data/sst_1d.png", dates: ["2026-04-26"] },
        "2d": { url: "/data/sst_2d.png", dates: ["2026-04-25", "2026-04-26"] },
        "3d": { url: "/data/sst_3d.png" },
      },
    },
    chl: {
      windows: {
        "2d": { url: "/data/chl_2d.png" },
      },
    },
    viz: {
      windows: {
        now: { url: "/data/viz_p50_ft.png" },
      },
    },
  },
};


function withFreshModule(fn) {
  jest.resetModules();
  const ds = require("../dataSource.js");
  return fn(ds);
}


function mockFetchOnce(payload) {
  global.fetch = jest.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(payload) })
  );
}


function mockFetchFailure() {
  global.fetch = jest.fn(() => Promise.reject(new Error("network down")));
}


describe("loadManifest", () => {
  it("fetches once and caches the result", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      await ds.loadManifest();
      await ds.loadManifest();
      expect(global.fetch).toHaveBeenCalledTimes(1);
      expect(ds.getManifest()).toEqual(SAMPLE_MANIFEST);
      expect(ds.isReady()).toBe(true);
    });
  });

  it("hits the live shouldidive.com manifest URL", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      const calledUrl = global.fetch.mock.calls[0][0];
      expect(calledUrl).toBe("https://shouldidive.com/data/manifest.json");
    });
  });

  it("dedupes concurrent calls (returns the inflight promise)", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      const [a, b, c] = await Promise.all([
        ds.loadManifest(),
        ds.loadManifest(),
        ds.loadManifest(),
      ]);
      expect(a).toBe(b);
      expect(b).toBe(c);
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });
  });

  it("survives fetch failure without crashing the whole app", async () => {
    mockFetchFailure();
    await withFreshModule(async (ds) => {
      const result = await ds.loadManifest();
      expect(result).toBeNull();
      expect(ds.isReady()).toBe(true); // ready=true even on failure
      expect(ds.getManifest()).toBeNull();
    });
  });
});


describe("subscribe", () => {
  it("notifies subscribers when manifest lands", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      const listener = jest.fn();
      ds.subscribe(listener);
      await ds.loadManifest();
      expect(listener).toHaveBeenCalled();
    });
  });

  it("returns an unsubscribe fn that actually unsubscribes", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      const listener = jest.fn();
      const unsub = ds.subscribe(listener);
      unsub();
      await ds.loadManifest();
      expect(listener).not.toHaveBeenCalled();
    });
  });

  it("a throwing listener doesn't break notification of others", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      const a = jest.fn(() => { throw new Error("boom"); });
      const b = jest.fn();
      ds.subscribe(a);
      ds.subscribe(b);
      await ds.loadManifest();
      expect(a).toHaveBeenCalled();
      expect(b).toHaveBeenCalled();
    });
  });
});


describe("getLayerPngUrl", () => {
  beforeEach(() => {
    mockFetchOnce(SAMPLE_MANIFEST);
  });

  it("returns null before the manifest loads", () => {
    return withFreshModule((ds) => {
      expect(ds.getLayerPngUrl("sst", 2)).toBeNull();
    });
  });

  it("resolves sst with the requested composite to an absolute https URL", async () => {
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      const url = ds.getLayerPngUrl("sst", 2);
      expect(url).toBe("https://shouldidive.com/data/sst_2d.png");
    });
  });

  it("supports all three composites for sst", async () => {
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getLayerPngUrl("sst", 1)).toBe("https://shouldidive.com/data/sst_1d.png");
      expect(ds.getLayerPngUrl("sst", 2)).toBe("https://shouldidive.com/data/sst_2d.png");
      expect(ds.getLayerPngUrl("sst", 3)).toBe("https://shouldidive.com/data/sst_3d.png");
    });
  });

  it("ignores the composite arg for viz (always uses 'now' slot)", async () => {
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getLayerPngUrl("viz", 1)).toBe("https://shouldidive.com/data/viz_p50_ft.png");
      expect(ds.getLayerPngUrl("viz", 2)).toBe("https://shouldidive.com/data/viz_p50_ft.png");
      expect(ds.getLayerPngUrl("viz", null)).toBe("https://shouldidive.com/data/viz_p50_ft.png");
    });
  });

  it("returns null for unknown layers (e.g. wind/swell, not yet wired)", async () => {
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getLayerPngUrl("wind", 2)).toBeNull();
      expect(ds.getLayerPngUrl("swell", 2)).toBeNull();
    });
  });

  it("returns null for a layer that's missing the requested slot", async () => {
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getLayerPngUrl("chl", 1)).toBeNull(); // only 2d in fixture
      expect(ds.getLayerPngUrl("chl", 3)).toBeNull();
    });
  });

  it("returns null when the manifest has no layers entry", async () => {
    mockFetchOnce({});
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getLayerPngUrl("sst", 2)).toBeNull();
    });
  });

  it("handles manifest paths that are already absolute URLs", async () => {
    mockFetchOnce({
      layers: {
        sst: { windows: { "2d": { url: "https://other.cdn/foo.png" } } },
      },
    });
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getLayerPngUrl("sst", 2)).toBe("https://other.cdn/foo.png");
    });
  });
});


describe("getGeneratedAt", () => {
  it("returns the manifest's generated_at after load", async () => {
    mockFetchOnce(SAMPLE_MANIFEST);
    await withFreshModule(async (ds) => {
      await ds.loadManifest();
      expect(ds.getGeneratedAt()).toBe("2026-04-27T12:00:00Z");
    });
  });

  it("returns null before the manifest loads", () => {
    return withFreshModule((ds) => {
      expect(ds.getGeneratedAt()).toBeNull();
    });
  });
});
