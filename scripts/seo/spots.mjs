// Spot roster + static spot-chart facts for the SEO pages.
//
// The roster comes from REGION_SAVED_SPOTS in src/lib/mapData.js so a spot
// added to the app gets a page on the next build. That module touches
// `window` at import time, so the object literal is extracted from the source
// text instead of imported. tests/seoPages.test.js fails if the extraction
// ever stops finding the CA and Baja lists.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

export function readSavedSpots(repoRoot) {
  const src = readFileSync(join(repoRoot, "src/lib/mapData.js"), "utf8");
  const start = src.indexOf("const REGION_SAVED_SPOTS = {");
  if (start < 0) throw new Error("REGION_SAVED_SPOTS not found in src/lib/mapData.js");
  let i = src.indexOf("{", start), depth = 0, end = -1;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === "/" && src[i + 1] === "/") { i = src.indexOf("\n", i); continue; }
    if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) { end = i + 1; break; }
  }
  const literal = src.slice(src.indexOf("{", start), end);
  // Our own source file, evaluated at build time: plain data literal only.
  return new Function(`return (${literal});`)();
}

// Readable names and URL slugs. Keys are app spot ids.
const DISPLAY = {
  ca: {
    mendocino: ["Mendocino", "North Coast"],
    saltpoint: ["Salt Point", "North Coast"],
    monterey: ["Monterey", "Monterey and Big Sur"],
    pointlobos: ["Point Lobos", "Monterey and Big Sur"],
    monastery: ["Monastery Beach", "Monterey and Big Sur"],
    jadecove: ["Jade Cove", "Monterey and Big Sur"],
    morro: ["Morro Bay", "Central Coast"],
    "pt-concep": ["Point Conception", "Central Coast"],
    refugio: ["Refugio", "Central Coast"],
    santabarb: ["Santa Barbara", "Central Coast"],
    anacapa: ["Anacapa Island", "Channel Islands"],
    santacruz: ["Santa Cruz Island", "Channel Islands"],
    santarosa: ["Santa Rosa Island", "Channel Islands"],
    sanmiguel: ["San Miguel Island", "Channel Islands"],
    sbisland: ["Santa Barbara Island", "Channel Islands"],
    sannicolas: ["San Nicolas Island", "Channel Islands"],
    sanclemente: ["San Clemente Island", "Channel Islands"],
    catalina: ["Catalina Island", "Channel Islands"],
    malibu: ["Malibu", "Los Angeles and Orange County"],
    palosverdes: ["Palos Verdes", "Los Angeles and Orange County"],
    redondo: ["Redondo Beach", "Los Angeles and Orange County"],
    laguna: ["Laguna Beach", "Los Angeles and Orange County"],
    lajolla: ["La Jolla", "San Diego"],
    sandiego: ["San Diego", "San Diego"],
    pointloma: ["Point Loma", "San Diego"],
    coronados: ["Coronado Islands", "San Diego"],
  },
  baja: {
    k38: ["K-38", "Baja Pacific coast"],
    salsipuedes: ["Salsipuedes", "Baja Pacific coast"],
    "quatro-casas": ["Quatro Casas", "Baja Pacific coast"],
    sacramento: ["Sacramento Reef", "Baja Pacific coast"],
    "santa-ros": ["Santa Rosalillita", "Baja Pacific coast"],
    "san-benito": ["Isla San Benito", "Baja Pacific coast"],
    cedros: ["Isla Cedros", "Baja Pacific coast"],
    "bahia-tort": ["Bahía Tortugas", "Baja Pacific coast"],
    abreojos: ["Punta Abreojos", "Baja Pacific coast"],
    scorpion: ["Scorpion Bay", "Baja Pacific coast"],
    "punta-conejo": ["Punta Conejo", "Baja Pacific coast"],
    "costa-azul": ["Costa Azul", "Los Cabos"],
    "cabo-falso": ["Cabo Falso", "Los Cabos"],
    cabo: ["Cabo San Lucas", "Los Cabos"],
    "gordo-banks": ["Gordo Banks", "Los Cabos"],
    "cabo-pulmo": ["Cabo Pulmo", "La Paz and the East Cape"],
    "los-islotes": ["Los Islotes", "La Paz and the East Cape"],
    "espiritu-stm": ["Isla Espíritu Santo", "La Paz and the East Cape"],
    "el-bajo": ["El Bajo seamount", "La Paz and the East Cape"],
    cerralvo: ["Isla Cerralvo", "La Paz and the East Cape"],
    "isla-carmen": ["Isla Carmen", "Loreto and Mulegé"],
    "isla-danzante": ["Isla Danzante", "Loreto and Mulegé"],
    loreto: ["Loreto", "Loreto and Mulegé"],
    "isla-tortuga": ["Isla Tortuga", "Loreto and Mulegé"],
    "isla-san-mar": ["Isla San Marcos", "Loreto and Mulegé"],
    "isla-tiburon": ["Isla Tiburón", "Midriff Islands and the northern Sea of Cortez"],
    "isla-ag": ["Isla Ángel de la Guarda", "Midriff Islands and the northern Sea of Cortez"],
    "isla-rasa": ["Isla Rasa", "Midriff Islands and the northern Sea of Cortez"],
    "bahia-angel": ["Bahía de los Ángeles", "Midriff Islands and the northern Sea of Cortez"],
    "roca-consag": ["Roca Consag", "Midriff Islands and the northern Sea of Cortez"],
    "san-felipe": ["San Felipe", "Midriff Islands and the northern Sea of Cortez"],
  },
};

export const REGION_LABEL = { ca: "California", baja: "Baja California" };

export function slugify(s) {
  return s
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

// Baja California (north of 28 N) keeps Pacific time; Baja California Sur
// is on Mountain time with no DST; Isla Tiburón belongs to Sonora.
export function spotTimeZone(region, spot) {
  if (region === "ca") return "America/Los_Angeles";
  if (spot.id === "isla-tiburon") return "America/Hermosillo";
  return spot.lat >= 28 ? "America/Tijuana" : "America/Mazatlan";
}

export function buildRoster(repoRoot, regions) {
  const saved = readSavedSpots(repoRoot);
  const roster = [];
  for (const region of regions) {
    for (const s of saved[region] || []) {
      const [name, group] = DISPLAY[region]?.[s.id] || [s.name, REGION_LABEL[region] || region];
      roster.push({
        ...s, region, name, appName: s.name, group,
        slug: slugify(name),
        tz: spotTimeZone(region, s),
      });
    }
  }
  const seen = new Set();
  for (const s of roster) {
    if (seen.has(s.slug)) s.slug = `${s.slug}-${s.region}`;
    seen.add(s.slug);
  }
  return roster;
}

// --- Detailed-chart bundle facts -------------------------------------------

function ringArea(ring, lat0) {
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110540;
  let a = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    a += (ring[j][0] * kx) * (ring[i][1] * ky) - (ring[i][0] * kx) * (ring[j][1] * ky);
  }
  return Math.abs(a) / 2;
}

function polygonArea(coords, lat0) {
  if (!coords?.length) return 0;
  let a = ringArea(coords[0], lat0);
  for (let k = 1; k < coords.length; k++) a -= ringArea(coords[k], lat0);
  return Math.max(0, a);
}

function featureArea(f, lat0) {
  const g = f.geometry;
  if (!g) return 0;
  if (g.type === "Polygon") return polygonArea(g.coordinates, lat0);
  if (g.type === "MultiPolygon") return g.coordinates.reduce((s, p) => s + polygonArea(p, lat0), 0);
  return 0;
}

const M2_PER_ACRE = 4046.8564224;

export function readBundleFacts(dataDir, region, spotId) {
  const dir = join(dataDir, region === "ca" ? "" : region, "spots", spotId);
  const bundlePath = join(dir, "bundle.json");
  if (!existsSync(bundlePath)) return null;
  const read = (f) => (existsSync(join(dir, f)) ? JSON.parse(readFileSync(join(dir, f), "utf8")) : null);
  const bundle = JSON.parse(readFileSync(bundlePath, "utf8"));
  const lat0 = bundle.centre?.lat ?? 0;
  const facts = { id: spotId, layers: Object.keys(bundle.layers || {}) };

  const soundings = read("soundings.geojson");
  if (soundings?.features?.length) {
    const d = soundings.features.map((f) => f.properties?.depth_ft).filter(Number.isFinite);
    if (d.length) facts.depthFt = { min: Math.min(...d), max: Math.max(...d), n: d.length };
  }
  const contours = bundle.layers?.contours;
  if (contours?.intervals_m) facts.contourIntervalsM = contours.intervals_m;

  const kelp = read("kelp.geojson");
  if (kelp?.features?.length) {
    let canopy = 0, former = 0;
    for (const f of kelp.features) {
      const cls = f.properties?.className || "";
      const a = featureArea(f, lat0);
      if (/former/i.test(cls)) former += a;
      else canopy += a;
    }
    facts.kelp = {
      canopyAcres: canopy / M2_PER_ACRE,
      formerAcres: former / M2_PER_ACRE,
      quarter: bundle.layers?.kelp?.canopy_quarter || null,
      source: bundle.layers?.kelp?.source || bundle.sources?.kelp || null,
    };
  }

  const mpa = read("mpa.geojson");
  if (mpa?.features?.length) {
    const byName = new Map();
    for (const f of mpa.features) {
      const p = f.properties || {};
      if (p.name && !byName.has(p.name)) byName.set(p.name, { name: p.name, type: p.type || null });
    }
    facts.mpas = [...byName.values()];
  }

  const landmarks = read("landmarks.geojson");
  if (landmarks?.features?.length) {
    facts.landmarks = landmarks.features.map((f) => f.properties?.name).filter(Boolean);
  }
  return facts;
}

export function haversineKm(a, b) {
  const R = 6371, toR = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toR, dLng = (b.lng - a.lng) * toR;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * toR) * Math.cos(b.lat * toR) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
