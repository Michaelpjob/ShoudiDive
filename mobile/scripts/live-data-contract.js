#!/usr/bin/env node

const MANIFEST_URL = "https://shouldidive.com/data/manifest.json";
const REMOTE_BASE = "https://shouldidive.com";
const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const EXPECTED_WINDOWS = {
  sst: ["1d", "2d", "3d"],
  chl: ["1d", "2d", "3d"],
  viz: ["now"],
};

function divider() {
  console.log("-".repeat(64));
}

function assetUrlFromWindow(window) {
  return window?.mobile_url || window?.color_url || window?.url || null;
}

function resolveAssetUrl(assetPath) {
  if (!assetPath) return null;
  if (/^https?:/i.test(assetPath)) return assetPath;
  return `${REMOTE_BASE}${assetPath.startsWith("/") ? "" : "/"}${assetPath}`;
}

function readPngSize(buffer) {
  if (buffer.length < 24) {
    throw new Error(`PNG too small (${buffer.length} bytes)`);
  }
  if (!buffer.subarray(0, 8).equals(PNG_SIG)) {
    throw new Error("missing PNG signature");
  }
  if (buffer.toString("ascii", 12, 16) !== "IHDR") {
    throw new Error("missing IHDR chunk");
  }
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} -> ${response.status}`);
  }
  return response.json();
}

async function fetchBinary(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} -> ${response.status}`);
  }

  const bytes = Buffer.from(await response.arrayBuffer());
  const contentType = response.headers.get("content-type") || "";
  return { bytes, contentType };
}

async function assertWindowAsset(layer, windowKey, windowInfo) {
  if (!windowInfo || typeof windowInfo !== "object") {
    throw new Error(`${layer}.${windowKey} is missing`);
  }

  if (!windowInfo.url) {
    throw new Error(`${layer}.${windowKey} is missing canonical url`);
  }

  const chosenAsset = assetUrlFromWindow(windowInfo);
  if (!chosenAsset) {
    throw new Error(`${layer}.${windowKey} has no usable asset url`);
  }

  const checks = [
    { label: "canonical", path: windowInfo.url },
  ];

  if (chosenAsset !== windowInfo.url) {
    checks.push({ label: "mobile", path: chosenAsset });
  }

  for (const check of checks) {
    const url = resolveAssetUrl(check.path);
    const { bytes, contentType } = await fetchBinary(url);
    const { width, height } = readPngSize(bytes);

    if (width < 64 || height < 64) {
      throw new Error(
        `${layer}.${windowKey} ${check.label} image too small (${width}x${height})`
      );
    }
    if (bytes.length < 1024) {
      throw new Error(
        `${layer}.${windowKey} ${check.label} image suspiciously small (${bytes.length} bytes)`
      );
    }

    console.log(
      `OK ${layer}.${windowKey} ${check.label} -> ${width}x${height}, ${bytes.length} bytes, ${contentType || "content-type unknown"}`
    );
  }
}

async function main() {
  console.log("Fetching live manifest...");
  const manifest = await fetchJson(MANIFEST_URL);

  if (!manifest?.generated_at) {
    throw new Error("manifest is missing generated_at");
  }
  if (!manifest?.layers || typeof manifest.layers !== "object") {
    throw new Error("manifest is missing layers");
  }

  console.log(`Manifest generated_at: ${manifest.generated_at}`);
  divider();

  for (const [layer, windows] of Object.entries(EXPECTED_WINDOWS)) {
    const layerInfo = manifest.layers[layer];
    if (!layerInfo) {
      throw new Error(`manifest.layers.${layer} is missing`);
    }
    for (const windowKey of windows) {
      await assertWindowAsset(layer, windowKey, layerInfo.windows?.[windowKey]);
    }
  }

  divider();
  console.log("OK Live data contract passed.");
}

main().catch((error) => {
  console.error(`Live data contract failed: ${error.message}`);
  process.exit(1);
});
