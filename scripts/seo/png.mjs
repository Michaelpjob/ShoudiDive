// Minimal PNG decoder for the build-time SEO page generator.
//
// The pipeline writes 8-bit, non-interlaced PNGs: grayscale (color type 0)
// for scalar layers (sst, chl, viz, wind speed, bathy) and RGBA (type 6) for
// packed layers (wave Hs/Tp/Dp, wind/current u/v). The browser decodes them
// through canvas.getImageData(); this returns the same RGBA byte layout so
// scripts/seo/sample.mjs can apply the exact decode math from
// src/lib/loaders/decoders.js. Node's zlib does the inflate, so no npm
// dependency is needed.

import { readFileSync } from "node:fs";
import { inflateSync } from "node:zlib";

const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const CHANNELS = { 0: 1, 2: 3, 4: 2, 6: 4 };

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

export function decodePngBuffer(buf) {
  if (!buf.subarray(0, 8).equals(SIGNATURE)) throw new Error("not a PNG");
  let pos = 8;
  let width = 0, height = 0, bitDepth = 0, colorType = 0, interlace = 0;
  let palette = null, trns = null;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString("latin1", pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      interlace = data[12];
    } else if (type === "PLTE") {
      palette = data;
    } else if (type === "tRNS") {
      trns = data;
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
    pos += 12 + len;
  }
  if (bitDepth !== 8) throw new Error(`unsupported bit depth ${bitDepth}`);
  if (interlace !== 0) throw new Error("interlaced PNG not supported");
  const ch = colorType === 3 ? 1 : CHANNELS[colorType];
  if (!ch) throw new Error(`unsupported color type ${colorType}`);

  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * ch;
  const px = Buffer.alloc(stride * height);
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const out = px.subarray(y * stride, (y + 1) * stride);
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? out[i - ch] : 0;
      const b = prev[i];
      const c = i >= ch ? prev[i - ch] : 0;
      let v = line[i];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) v += paeth(a, b, c);
      out[i] = v & 0xff;
    }
    prev = out;
  }

  // Expand to RGBA, matching what canvas.getImageData() hands the browser.
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    const o = i * 4;
    if (colorType === 0) {
      const g = px[i];
      rgba[o] = rgba[o + 1] = rgba[o + 2] = g;
      rgba[o + 3] = 255;
    } else if (colorType === 4) {
      const g = px[i * 2];
      rgba[o] = rgba[o + 1] = rgba[o + 2] = g;
      rgba[o + 3] = px[i * 2 + 1];
    } else if (colorType === 2) {
      rgba[o] = px[i * 3];
      rgba[o + 1] = px[i * 3 + 1];
      rgba[o + 2] = px[i * 3 + 2];
      rgba[o + 3] = 255;
    } else if (colorType === 6) {
      rgba[o] = px[i * 4];
      rgba[o + 1] = px[i * 4 + 1];
      rgba[o + 2] = px[i * 4 + 2];
      rgba[o + 3] = px[i * 4 + 3];
    } else {
      const idx = px[i];
      rgba[o] = palette[idx * 3];
      rgba[o + 1] = palette[idx * 3 + 1];
      rgba[o + 2] = palette[idx * 3 + 2];
      rgba[o + 3] = trns && idx < trns.length ? trns[idx] : 255;
    }
  }
  return { width, height, data: rgba };
}

export function decodePngFile(path) {
  return decodePngBuffer(readFileSync(path));
}
