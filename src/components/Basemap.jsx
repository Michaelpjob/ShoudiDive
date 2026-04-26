import { useMemo } from "react";
import { project, COASTLINE, ISLANDS, BBOX } from "../lib/mapData.js";

function smoothPath(points) {
  if (points.length < 2) return "";
  const d = [`M ${points[0][0]} ${points[0][1]}`];
  for (let i = 1; i < points.length - 1; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[i + 1];
    const cx = (x1 + x2) / 2;
    const cy = (y1 + y2) / 2;
    d.push(`Q ${x1} ${y1} ${cx} ${cy}`);
  }
  const last = points[points.length - 1];
  d.push(`T ${last[0]} ${last[1]}`);
  return d.join(" ");
}

export default function Basemap({ width, height }) {
  const coastPts = useMemo(
    () => COASTLINE.map(([lng, lat]) => project(lng, lat, width, height)),
    [width, height]
  );

  const landPath = useMemo(() => {
    const pts = coastPts.slice();
    pts.push([width + 60, pts[pts.length - 1][1]]);
    pts.push([width + 60, -60]);
    pts.push([pts[0][0], -60]);
    return (
      smoothPath(pts) +
      ` L ${width + 60} ${pts[pts.length - 1][1]} L ${width + 60} -60 L ${pts[0][0]} -60 Z`
    );
  }, [coastPts, width]);

  const coastStroke = useMemo(() => smoothPath(coastPts), [coastPts]);

  const bathy = useMemo(() => {
    const offsets = [22, 60, 120, 200];
    return offsets.map((off) => coastPts.map(([x, y]) => [x - off, y]));
  }, [coastPts]);

  const graticule = useMemo(() => {
    const lines = [];
    for (let lat = Math.ceil(BBOX.latMin); lat <= Math.floor(BBOX.latMax); lat++) {
      const [, y] = project(BBOX.lngMin, lat, width, height);
      lines.push({ kind: "lat", v: lat, y });
    }
    for (let lng = Math.ceil(BBOX.lngMin); lng <= Math.floor(BBOX.lngMax); lng++) {
      const [x] = project(lng, BBOX.latMin, width, height);
      lines.push({ kind: "lng", v: lng, x });
    }
    return lines;
  }, [width, height]);

  const labels = [
    { text: "MONTEREY BAY",       lng: -121.95, lat: 36.78, size: 9, weight: 500 },
    { text: "BIG SUR",            lng: -121.65, lat: 36.10, size: 9 },
    { text: "MORRO BAY",          lng: -120.82, lat: 35.36, size: 9 },
    { text: "PT. CONCEPTION",     lng: -120.42, lat: 34.46, size: 9, weight: 500 },
    { text: "SANTA BARBARA",      lng: -119.70, lat: 34.46, size: 9 },
    { text: "LOS ANGELES",        lng: -118.20, lat: 34.10, size: 10, weight: 500 },
    { text: "LA JOLLA",           lng: -117.20, lat: 32.86, size: 9 },
    { text: "SAN DIEGO",          lng: -117.10, lat: 32.65, size: 9, weight: 500 },
    { text: "CHANNEL ISLANDS",    lng: -119.85, lat: 33.78, size: 8.5, italic: true, color: "var(--ink-3)" },
    { text: "SOUTHERN CA BIGHT",  lng: -118.95, lat: 33.20, size: 9, italic: true, color: "var(--ink-3)" },
    { text: "PACIFIC OCEAN",      lng: -122.40, lat: 35.20, size: 11, italic: true, color: "var(--ink-3)", letterSpacing: "0.2em" },
  ];

  return (
    <g className="basemap">
      <rect x="0" y="0" width={width} height={height} fill="var(--sea-deeper)" />

      <defs>
        <linearGradient id="seaBands" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--sea-deeper)" />
          <stop offset="60%" stopColor="var(--sea-deep)" />
          <stop offset="100%" stopColor="var(--sea)" />
        </linearGradient>
        <pattern id="oceanTexture" x="0" y="0" width="60" height="60" patternUnits="userSpaceOnUse">
          <rect width="60" height="60" fill="transparent" />
          <circle cx="30" cy="30" r="0.5" fill="var(--bathy-line)" opacity="0.6" />
        </pattern>
        <clipPath id="seaClip">
          <rect x="0" y="0" width={width} height={height} />
        </clipPath>
      </defs>
      <rect x="0" y="0" width={width} height={height} fill="url(#seaBands)" opacity="0.85" />
      <rect x="0" y="0" width={width} height={height} fill="url(#oceanTexture)" />

      <g className="graticule" stroke="var(--grid)" strokeWidth="0.6">
        {graticule.map((g, i) =>
          g.kind === "lat" ? (
            <g key={"lat" + i}>
              <line x1="0" y1={g.y} x2={width} y2={g.y} strokeDasharray="2 4" />
              <text
                x={8}
                y={g.y - 3}
                fontSize="9"
                fill="var(--ink-3)"
                fontFamily="JetBrains Mono, monospace"
                opacity="0.7"
              >
                {g.v}°N
              </text>
            </g>
          ) : (
            <g key={"lng" + i}>
              <line x1={g.x} y1="0" x2={g.x} y2={height} strokeDasharray="2 4" />
              <text
                x={g.x + 4}
                y={height - 8}
                fontSize="9"
                fill="var(--ink-3)"
                fontFamily="JetBrains Mono, monospace"
                opacity="0.7"
              >
                {g.v}°W
              </text>
            </g>
          )
        )}
      </g>

      <g className="bathy" fill="none" stroke="var(--bathy-line)" strokeWidth="0.7">
        {bathy.map((pts, i) => (
          <path key={i} d={smoothPath(pts)} opacity={0.5 - i * 0.08} />
        ))}
      </g>

      <g className="islands">
        {ISLANDS.map((isle, i) => {
          const [cx, cy] = project(isle.lng, isle.lat, width, height);
          const [edx, edy] = project(isle.lng + isle.rx, isle.lat - isle.ry, width, height);
          const rx = Math.abs(edx - cx);
          const ry = Math.abs(edy - cy);
          return (
            <ellipse
              key={i}
              cx={cx}
              cy={cy}
              rx={rx}
              ry={ry}
              fill="var(--land)"
              stroke="var(--land-edge)"
              strokeWidth="0.8"
            />
          );
        })}
      </g>

      <path d={landPath} fill="var(--land)" stroke="var(--land-edge)" strokeWidth="1" />
      <path d={coastStroke} fill="none" stroke="var(--land-edge)" strokeWidth="1.2" opacity="0.9" />

      <g className="map-labels">
        {labels.map((lab, i) => {
          const [x, y] = project(lab.lng, lab.lat, width, height);
          return (
            <text
              key={i}
              x={x}
              y={y}
              fontSize={lab.size || 9}
              fontWeight={lab.weight || 400}
              fontStyle={lab.italic ? "italic" : "normal"}
              fontFamily="Inter, sans-serif"
              fill={lab.color || "var(--ink-2)"}
              letterSpacing={lab.letterSpacing || "0.05em"}
              textAnchor="middle"
              style={{
                paintOrder: "stroke",
                stroke: "var(--bg)",
                strokeWidth: 3,
                strokeLinejoin: "round",
              }}
            >
              {lab.text}
            </text>
          );
        })}
      </g>
    </g>
  );
}
