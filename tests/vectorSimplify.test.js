// Unit tests for src/lib/vectorSimplify.js — PR-K3-2 of the kelp roadmap.
//
// We exercise the public surface:
//   * simplifyRing  — Douglas-Peucker on a single ring
//   * simplifyGeometry — Polygon + MultiPolygon dispatch + passthrough
//   * toleranceForZoom — zoom → tolerance band mapping
//
// The simplifier is foundational for the kelp canopy layer (Phase 3)
// and used by the admin-bed + MPA layers today. Regressions show up
// as either dropped polygons (under-shoots min ring length) or jagged
// edges (over-aggressive at low zoom). Both fail this suite.

import assert from "node:assert/strict";
import test from "node:test";

import {
  simplifyRing,
  simplifyGeometry,
  toleranceForZoom,
} from "../src/lib/vectorSimplify.js";

test("simplifyRing returns the input for tiny rings (no degeneration)", () => {
  const tri = [
    [0, 0],
    [1, 0],
    [0.5, 1],
    [0, 0],
  ];
  assert.deepEqual(simplifyRing(tri, 0.5), tri);
});

test("simplifyRing returns the input for tolerance <= 0", () => {
  const ring = [
    [0, 0],
    [1, 0],
    [1, 1],
    [0, 1],
    [0, 0],
  ];
  assert.deepEqual(simplifyRing(ring, 0), ring);
  assert.deepEqual(simplifyRing(ring, -1), ring);
});

test("simplifyRing drops collinear interior points at high tolerance", () => {
  // 5-vertex ring whose middle three points are nearly collinear.
  const ring = [
    [0, 0],
    [0.25, 0.001],
    [0.5, 0.001],
    [0.75, 0.001],
    [1, 0],
    [0.5, 1],
    [0, 0],
  ];
  // tolerance 0.5 should strip the collinear middle three vertices.
  const simplified = simplifyRing(ring, 0.5);
  assert.ok(
    simplified.length < ring.length,
    `expected simplification to drop vertices; got ${simplified.length}/${ring.length}`
  );
  // Closing duplicate is preserved.
  assert.deepEqual(simplified[0], simplified[simplified.length - 1]);
});

test("simplifyRing keeps perpendicular features above tolerance", () => {
  // A ring with a sharp spike — the spike's apex must NOT be dropped
  // even at moderate tolerance.
  const ring = [
    [0, 0],
    [0.5, 0],
    [0.5, 5], // spike apex (perpendicular distance 5 from base segment)
    [0.5, 0],
    [1, 0],
    [0, 0],
  ];
  const simplified = simplifyRing(ring, 0.5);
  const hasSpike = simplified.some((p) => p[0] === 0.5 && p[1] === 5);
  assert.ok(hasSpike, "spike apex (perpendicular dist 5) must survive tolerance 0.5");
});

test("simplifyGeometry handles Polygon", () => {
  const geom = {
    type: "Polygon",
    coordinates: [
      [
        [0, 0],
        [0.1, 0.001],
        [0.5, 0.001],
        [1, 0],
        [0.5, 1],
        [0, 0],
      ],
    ],
  };
  const out = simplifyGeometry(geom, 0.5);
  assert.equal(out.type, "Polygon");
  assert.ok(Array.isArray(out.coordinates[0]));
  assert.ok(out.coordinates[0].length <= geom.coordinates[0].length);
});

test("simplifyGeometry handles MultiPolygon", () => {
  const geom = {
    type: "MultiPolygon",
    coordinates: [
      [
        [
          [0, 0],
          [0.1, 0.001],
          [1, 0],
          [0.5, 1],
          [0, 0],
        ],
      ],
      [
        [
          [2, 2],
          [3, 2.001],
          [3, 3],
          [2, 3],
          [2, 2],
        ],
      ],
    ],
  };
  const out = simplifyGeometry(geom, 0.5);
  assert.equal(out.type, "MultiPolygon");
  assert.equal(out.coordinates.length, 2);
});

test("simplifyGeometry passes through non-polygon types unchanged", () => {
  const pt = { type: "Point", coordinates: [1, 2] };
  assert.deepEqual(simplifyGeometry(pt, 0.5), pt);
  const ls = { type: "LineString", coordinates: [[0, 0], [1, 1]] };
  assert.deepEqual(simplifyGeometry(ls, 0.5), ls);
});

test("toleranceForZoom maps zoom bands correctly", () => {
  // Five-band ladder — assert each band returns its expected tolerance.
  assert.equal(toleranceForZoom(1),  0.01);   // overview
  assert.equal(toleranceForZoom(2),  0.005);
  assert.equal(toleranceForZoom(4),  0.001);
  assert.equal(toleranceForZoom(8),  0.0004);
  assert.equal(toleranceForZoom(12), 0.0001);
  assert.equal(toleranceForZoom(16), 0.0001);  // saturates at finest band
  // Edge cases — non-finite / non-positive zoom defaults to 1× band.
  assert.equal(toleranceForZoom(undefined), 0.01);
  assert.equal(toleranceForZoom(null), 0.01);
  assert.equal(toleranceForZoom(-1), 0.01);
  assert.equal(toleranceForZoom(NaN), 0.01);
});

test("simplifyRing of a coarse box at high tolerance keeps all four corners", () => {
  // The 4 corners of a square are NOT collinear — DP must keep them
  // regardless of tolerance (none lie on the segment between the
  // adjacent two).
  const box = [
    [0, 0],
    [1, 0],
    [1, 1],
    [0, 1],
    [0, 0],
  ];
  const simplified = simplifyRing(box, 100);
  assert.equal(simplified.length, box.length, "all 4 corners + closing must survive");
});
