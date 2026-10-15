/* Ocean Report: what the water, the weather and the fleet are doing, and how
   it moved. Renders /data/reports/<date>.json (pipeline/reports/build_report.py).
   Every string from the data passes through esc(); the page adds no numbers. */
(function () {
  "use strict";
  var DATA = "/data/reports/";
  var VIEWS = [["fishing", "Fishing"], ["diving", "Diving"], ["spearfishing", "Spearfishing"]];
  // Dark-surface categorical steps (dataviz reference palette, validated order).
  var C = { s1: "#3987e5", s2: "#d95926", s3: "#199e70", s4: "#c98500" };
  // The main map's SST ramp (src/lib/mapData.js SST_STOPS), stretched to SoCal fall water.
  var SST_STOPS = [[0, [12, 38, 130]], [0.25, [40, 130, 210]], [0.5, [120, 220, 220]], [0.7, [240, 220, 110]], [0.85, [230, 110, 60]], [1, [170, 20, 35]]];
  var SST_SHOW_F = [60, 76];
  // Fleet stops by age: one orange hue, darkest for today (sequential, not a rating).
  var AGE_COLORS = ["#d95926", "#e9875a", "#f2ae8b", "#f7d1bd"];
  var gpsMarkers = null;
  var BUOY_SHORT = { "46254": "La Jolla", "46232": "Point Loma S", "46086": "S. Clemente Basin", "46047": "Tanner Bank", "46253": "San Pedro S" };
  var main = document.getElementById("report");
  var picker = document.getElementById("date");
  var facts = null, view = initialView(), map = null, CHARTS = {}, chartSeq = 0;

  function initialView() {
    var m = /view=(fishing|diving|spearfishing)/.exec(location.hash);
    if (m) return m[1];
    try { var v = localStorage.getItem("sd:report:view"); if (v) return v; } catch (e) { /* storage blocked */ }
    return "fishing";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function link(url, text) {
    if (!url) return esc(text);
    var u = String(url);
    if (!/^https?:\/\//.test(u) && u.charAt(0) !== "/") return esc(text);
    return '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(text) + "</a>";
  }
  function localTime(iso) {
    if (!iso) return "";
    var s = String(iso);
    var d = new Date(/T\d{2}:\d{2}Z$/.test(s) ? s.replace(/Z$/, ":00Z") : s);
    if (isNaN(d)) return iso;
    return d.toLocaleString("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function md(iso) {
    var d = new Date(String(iso).slice(0, 10) + "T12:00:00Z");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  }
  function signed(x, unit) { return (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(1) + (unit || ""); }
  function colorWord(chl) { return chl == null ? "" : chl <= 0.2 ? "blue" : chl <= 0.5 ? "blue-green" : chl <= 1.5 ? "green" : "dirty green"; }
  function tagHtml(t) { return '<span class="tag">' + esc(t) + "</span>"; }

  // --- Charts (inline SVG) ------------------------------------------------------
  function spark(vals, color, w, h) {
    w = w || 120; h = h || 28;
    var pts = vals.map(function (v, i) { return v == null ? null : [i, v]; }).filter(Boolean);
    if (pts.length < 2) return "";
    var lo = Math.min.apply(null, pts.map(function (p) { return p[1]; })), hi = Math.max.apply(null, pts.map(function (p) { return p[1]; }));
    if (hi - lo < 0.5) { lo -= 0.25; hi += 0.25; }
    var X = function (i) { return 3 + (i / (vals.length - 1)) * (w - 6); };
    var Y = function (v) { return h - 3 - ((v - lo) / (hi - lo)) * (h - 6); };
    var d = pts.map(function (p, k) { return (k ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); }).join(" ");
    var last = pts[pts.length - 1];
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" aria-hidden="true"><path d="' + d +
      '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="' + X(last[0]).toFixed(1) +
      '" cy="' + Y(last[1]).toFixed(1) + '" r="3" fill="' + color + '" stroke="#0b1220" stroke-width="2"/></svg>';
  }
  function bars(vals, color, w, h, label) {
    w = w || 140; h = h || 28;
    var hi = Math.max.apply(null, vals.concat([0.0001]));
    var bw = (w - (vals.length - 1) * 2) / vals.length;
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label || "") + '">' +
      '<line x1="0" x2="' + w + '" y1="' + (h - 0.5) + '" y2="' + (h - 0.5) + '" stroke="#334155" stroke-width="1"/>' +
      vals.map(function (v, i) {
        var bh = v > 0 ? Math.max(2, (v / hi) * (h - 2)) : 0;
        return bh ? '<rect x="' + (i * (bw + 2)).toFixed(1) + '" y="' + (h - bh).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="1.5" fill="' + color + '"/>' : "";
      }).join("") + "</svg>";
  }

  // Line and range charts: the figure shell is built with the page, then each
  // plot is drawn at its real pixel width (so text stays 11-12 px on phones and
  // desktops) on a true time axis, breaking the line where days are missing.
  function dayNum(d) { return Date.parse(String(d).slice(0, 10) + "T00:00:00Z") / 864e5; }

  function lineChart(spec) {
    var id = "ch" + (++chartSeq);
    var dates = [];
    spec.series.forEach(function (s) { s.points.forEach(function (p) { if (dates.indexOf(p.d) < 0) dates.push(p.d); }); });
    dates.sort();
    if (dates.length < 2) return "";
    spec.dates = dates;
    CHARTS[id] = spec;
    var legend = spec.series.length > 1 ? '<div class="legend-row">' + spec.series.map(function (s) {
      return '<span class="key"><i style="background:' + esc(s.color) + '"></i>' + esc(s.name) + "</span>";
    }).join("") + "</div>" : "";
    var table = '<details class="data"><summary>Data</summary><div class="scroll"><table class="mini"><thead><tr><th>Date</th>' +
      spec.series.map(function (s) { return "<th>" + esc(s.short || s.name) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      dates.map(function (d) {
        return "<tr><td>" + esc(md(d)) + "</td>" + spec.series.map(function (s) {
          var p = s.points.filter(function (q) { return q.d === d; })[0];
          return "<td>" + (p ? esc(p.v != null ? p.v : [p.lo, p.hi].join("-")) : "") + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table></div></details>";
    return '<figure class="chart" data-chart="' + id + '"><figcaption>' + esc(spec.title) + (spec.sub ? '<span class="mut"> · ' + esc(spec.sub) + "</span>" : "") +
      "</figcaption>" + legend + '<div class="plot" role="img" aria-label="' + esc(spec.title) + '"></div>' + table + "</figure>";
  }

  function drawChart(fig) {
    var spec = CHARTS[fig.getAttribute("data-chart")];
    var plot = fig.querySelector(".plot");
    if (!spec || !plot) return;
    var W = Math.max(280, plot.clientWidth || 600), H = spec.height || 200, L = 34, R = spec.series.length > 1 || (spec.refs || []).length ? 112 : 70, T = 10, B = 22;
    var vals = [];
    spec.series.forEach(function (s) { s.points.forEach(function (p) { [p.v, p.lo, p.hi].forEach(function (v) { if (v != null) vals.push(v); }); }); });
    (spec.refs || []).forEach(function (r) { vals.push(r.v); });
    if (!vals.length) return;
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (spec.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    var pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    var d0 = dayNum(spec.dates[0]), d1 = dayNum(spec.dates[spec.dates.length - 1]);
    var X = function (d) { return L + (dayNum(d) - d0) / Math.max(1, d1 - d0) * (W - L - R); };
    var Y = function (v) { var t = (v - lo) / (hi - lo); return spec.invert ? T + t * (H - T - B) : H - B - t * (H - T - B); };
    var g = "";
    [0, 0.5, 1].forEach(function (k) {
      var v = lo + pad + k * (hi - lo - 2 * pad), y = Y(v);
      g += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '" stroke="#1e293b"/>' +
        '<text x="' + (L - 6) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" class="ax">' + esc(v.toFixed(spec.decimals == null ? 0 : spec.decimals)) + "</text>";
    });
    if (spec.zero) g += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(0).toFixed(1) + '" y2="' + Y(0).toFixed(1) + '" stroke="#475569" stroke-dasharray="3 3"/>';
    (spec.refs || []).forEach(function (r) {
      g += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(r.v).toFixed(1) + '" y2="' + Y(r.v).toFixed(1) + '" stroke="#475569" stroke-dasharray="3 3"/>' +
        '<text x="' + (W - R + 6) + '" y="' + (Y(r.v) + 4).toFixed(1) + '" class="ax">' + esc(r.label) + "</text>";
    });
    g += '<text x="' + L + '" y="' + (H - 5) + '" class="ax">' + esc(md(spec.dates[0])) + '</text><text x="' + (W - R) + '" y="' + (H - 5) +
      '" text-anchor="end" class="ax">' + esc(md(spec.dates[spec.dates.length - 1])) + "</text>";
    var ends = [];
    spec.series.forEach(function (s) {
      if (s.range) {
        s.points.forEach(function (p) {
          var x = X(p.d), y0 = Y(p.lo), y1 = Y(p.hi), top = Math.min(y0, y1), hgt = Math.max(4, Math.abs(y1 - y0));
          g += '<rect x="' + (x - 3).toFixed(1) + '" y="' + top.toFixed(1) + '" width="6" height="' + hgt.toFixed(1) + '" rx="3" fill="' + esc(s.color) + '"/>';
        });
        return;
      }
      var pts = s.points.filter(function (p) { return p.v != null; });
      if (!pts.length) return;
      var steps = pts.slice(1).map(function (p, k) { return dayNum(p.d) - dayNum(pts[k].d); }).sort(function (a, b) { return a - b; });
      var gap = Math.max(1.5, (steps[Math.floor(steps.length / 2)] || 1) * 1.5);
      var d = pts.map(function (p, k) {
        var brk = !k || dayNum(p.d) - dayNum(pts[k - 1].d) > gap;
        return (brk ? "M" : "L") + X(p.d).toFixed(1) + " " + Y(p.v).toFixed(1);
      }).join(" ");
      g += '<path d="' + d + '" fill="none" stroke="' + esc(s.color) + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
      var last = pts[pts.length - 1];
      ends.push({ y: Y(last.v), x: X(last.d), name: s.short || s.name, color: s.color });
    });
    ends.sort(function (a, b) { return a.y - b.y; });
    for (var i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    ends.forEach(function (e) {
      g += '<circle cx="' + e.x.toFixed(1) + '" cy="' + e.y.toFixed(1) + '" r="3" fill="' + esc(e.color) + '" stroke="#0b1220" stroke-width="2"/>' +
        '<text x="' + (W - R + 6) + '" y="' + (e.y + 4).toFixed(1) + '" class="lbl">' + esc(e.name) + "</text>";
    });
    g += '<line class="xh" x1="0" x2="0" y1="' + T + '" y2="' + (H - B) + '" stroke="#94a3b8" stroke-width="1" visibility="hidden"/>';
    plot.innerHTML = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '">' + g +
      '<rect x="' + L + '" y="0" width="' + (W - L - R) + '" height="' + H + '" fill="transparent"/></svg><div class="tip" hidden></div>';
    var svg = plot.querySelector("svg"), tip = plot.querySelector(".tip"), xh = plot.querySelector(".xh");
    function move(ev) {
      var r = svg.getBoundingClientRect();
      var pt = ev.touches ? ev.touches[0] : ev;
      var cx = pt.clientX - r.left, best = null;
      spec.dates.forEach(function (dd) { var dx = Math.abs(X(dd) - cx); if (!best || dx < best[0]) best = [dx, dd]; });
      var dsel = best[1], x = X(dsel);
      xh.setAttribute("x1", x); xh.setAttribute("x2", x); xh.setAttribute("visibility", "visible");
      tip.innerHTML = '<div class="mut">' + esc(md(dsel)) + "</div>" + spec.series.map(function (s) {
        var p = s.points.filter(function (q) { return q.d === dsel; })[0];
        if (!p) return "";
        var val = (p.v != null ? String(p.v) : [p.lo, p.hi].join("-")) + (spec.unit || "");
        return '<div><i style="background:' + esc(s.color) + '"></i>' + esc(s.short || s.name) + " <b>" + esc(val) + "</b></div>";
      }).join("");
      tip.hidden = false;
      tip.style.left = Math.min(Math.max(x + 10, 0), Math.max(0, W - 170)) + "px";
    }
    svg.addEventListener("mousemove", move);
    svg.addEventListener("touchmove", move, { passive: true });
    svg.addEventListener("mouseleave", function () { tip.hidden = true; xh.setAttribute("visibility", "hidden"); });
  }

  function bindCharts() {
    Array.prototype.forEach.call(main.querySelectorAll("figure.chart"), drawChart);
  }
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(bindCharts, 200);
  });

  // --- Sections -------------------------------------------------------------------
  function highlights() {
    var lines = (facts.highlights && facts.highlights[view]) || [];
    return '<section class="hl"><ul>' + lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul></section>";
  }

  function mapSection() {
    return '<section><h2 id="map-h">Where things are</h2><div id="map" class="map" role="region" aria-label="Map of water temperature, zones, breaks, paddies and fleet"></div>' +
      (view === "fishing" ? '<div class="maplegend"><span class="mut small">' + SST_SHOW_F[0] + '°F</span><span class="ramp" aria-hidden="true"></span><span class="mut small">' + SST_SHOW_F[1] + "°F</span></div>"
        : '<div class="maplegend"><span class="mut small">Predicted visibility ' + VIS_SHOW_FT[0] + ' ft</span><span class="ramp vis" aria-hidden="true"></span><span class="mut small">' + VIS_SHOW_FT[1] + "+ ft</span></div>") +
      '<div class="maplegend"><span class="mut small">Fleet stops:</span>' + ["today", "1 day", "2 days", "3 days"].map(function (t, i) {
        return '<span class="key"><i style="background:' + AGE_COLORS[i] + '"></i>' + t + "</span>";
      }).join("") + '<span class="key"><i style="background:#fbbf24"></i>boat now</span><span class="key"><i class="ring"></i>break</span><span class="key"><i style="background:#22c55e"></i>paddies</span></div>' +
      '<p class="note">' + (view === "fishing" ? "Water temperature: " + esc(sstAsOf()) + " satellite map; blank is cloud or no data."
        : "Predicted visibility: our model scaled to where recent La Jolla shop reports landed; island and offshore numbers extend that La Jolla fit. Water temperature is in the layer menu.") + "</p></section>";
  }

  // Predicted visibility overlay: one blue hue, pale for murky, deep for clear
  // (dataviz sequential blue, 100 -> 700), after the model's recent bias.
  var VIS_RAMP = [[205, 226, 251], [158, 197, 244], [109, 167, 236], [57, 135, 229], [37, 106, 191], [24, 79, 149], [13, 54, 107]];
  var VIS_SHOW_FT = [0, 35];
  function visColor(ft) {
    var t = Math.max(0, Math.min(1, (ft - VIS_SHOW_FT[0]) / (VIS_SHOW_FT[1] - VIS_SHOW_FT[0]))) * (VIS_RAMP.length - 1);
    var i = Math.min(Math.floor(t), VIS_RAMP.length - 2), k = t - i, a = VIS_RAMP[i], b = VIS_RAMP[i + 1];
    return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  }
  function drawVis(V, bb, layer) {
    var scale = (V.calibration && V.calibration.applied) ? V.calibration.scale : 1;
    var rng = V.range_ft || [0, 80];
    var img = new Image();
    img.onload = function () {
      var cv = document.createElement("canvas");
      cv.width = img.width; cv.height = img.height;
      var ctx = cv.getContext("2d");
      ctx.drawImage(img, 0, 0);
      var px = ctx.getImageData(0, 0, cv.width, cv.height), d = px.data;
      for (var i = 0; i < d.length; i += 4) {
        var v = d[i];
        if (!v) { d[i + 3] = 0; continue; }
        var ft = Math.max(0, (rng[0] + (v - 1) / 254 * (rng[1] - rng[0])) * scale);
        var c = visColor(ft);
        d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 210;
      }
      ctx.putImageData(px, 0, 0);
      L.imageOverlay(cv.toDataURL(), [[bb[1], bb[0]], [bb[3], bb[2]]], { className: "pix", interactive: false }).addTo(layer);
    };
    img.src = V.url;
  }

  function sstAsOf() {
    var s = (facts.fishing || {}).sst_field || {};
    return s.date_label ? [s.date_label, s.age_days > 1 ? ["(" + String(s.age_days), "days old)"].join(" ") : ""].join(" ").trim() : "latest";
  }
  function srcTime(id) {
    var s = (facts.sources || []).filter(function (x) { return x.id === id; })[0];
    return s && s.time_utc ? localTime(s.time_utc) : "";
  }

  function rampColor(t) {
    t = Math.max(0, Math.min(1, t));
    for (var i = 0; i < SST_STOPS.length - 1; i++) {
      var a = SST_STOPS[i], b = SST_STOPS[i + 1];
      if (t >= a[0] && t <= b[0]) {
        var k = (t - a[0]) / (b[0] - a[0]);
        return [a[1][0] + (b[1][0] - a[1][0]) * k, a[1][1] + (b[1][1] - a[1][1]) * k, a[1][2] + (b[1][2] - a[1][2]) * k];
      }
    }
    return SST_STOPS[SST_STOPS.length - 1][1];
  }
  function drawSst(M, layer) {
    var img = new Image();
    img.onload = function () {
      var cv = document.createElement("canvas");
      cv.width = img.width; cv.height = img.height;
      var ctx = cv.getContext("2d");
      ctx.drawImage(img, 0, 0);
      var px = ctx.getImageData(0, 0, cv.width, cv.height), d = px.data;
      var lo = M.sst_range_c[0], hi = M.sst_range_c[1];
      for (var i = 0; i < d.length; i += 4) {
        var v = d[i];
        if (!v) { d[i + 3] = 0; continue; }
        var f = (lo + (v - 1) / 254 * (hi - lo)) * 9 / 5 + 32;
        var c = rampColor((f - SST_SHOW_F[0]) / (SST_SHOW_F[1] - SST_SHOW_F[0]));
        d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 215;
      }
      ctx.putImageData(px, 0, 0);
      var bb = M.bbox; // [lng0, lat0, lng1, lat1]
      L.imageOverlay(cv.toDataURL(), [[bb[1], bb[0]], [bb[3], bb[2]]], { className: "pix", interactive: false }).addTo(layer);
    };
    img.src = M.sst_url;
  }

  function initMap() {
    var el = document.getElementById("map");
    if (!el || !window.L) return;
    if (map) { map.remove(); map = null; }
    map = L.map(el, { zoomControl: true, attributionControl: false, scrollWheelZoom: false });
    map.fitBounds(view === "fishing" ? [[31.9, -119.4], [33.6, -117.0]] : [[32.55, -117.95], [33.6, -117.1]]);
    var M = facts.map || {};
    var sstLayer = L.layerGroup().addTo(map), landLayer = L.layerGroup().addTo(map), padLayer = L.layerGroup().addTo(map),
      zoneLayer = L.layerGroup().addTo(map), fleetLayer = L.layerGroup().addTo(map), liveLayer = L.layerGroup().addTo(map), spotLayer = L.layerGroup();
    var V = facts.visibility || {};
    var visLayer = L.layerGroup();
    if (M.sst_url && M.bbox && M.sst_range_c) drawSst(M, sstLayer);
    if (V.url && M.bbox) drawVis(V, M.bbox, visLayer);
    if (view !== "fishing") { map.removeLayer(sstLayer); visLayer.addTo(map); }
    fetch("/data/land.geojson").then(function (r) { return r.ok ? r.json() : null; }).then(function (g) {
      if (g) L.geoJSON(g, { style: { color: "#475569", weight: 0.6, fillColor: "#1f2937", fillOpacity: 1 }, interactive: false }).addTo(landLayer);
    }).catch(function () { /* map still useful without land */ });
    L.polyline([[32.5344, -117.1249], [32.58948, -117.46373], [32.62694, -117.82528], [31.13278, -118.605]],
      { color: "#f87171", weight: 1.5, dashArray: "6 6", opacity: 0.8, interactive: false }).addTo(map);
    (M.paddies || []).forEach(function (p) {
      L.polygon(p.ring, { color: "#4ade80", weight: 1, fillColor: "#22c55e", fillOpacity: p.level <= 0.3 ? 0.35 : 0.12, interactive: false }).addTo(padLayer);
    });
    (facts.zones || []).forEach(function (z) {
      L.circle(z.center, { radius: z.radius_nm * 1852, color: "#cbd5e1", weight: 1, opacity: 0.5, fill: false, dashArray: "4 5" })
        .bindTooltip(esc(z.name) + (z.temp_f != null ? " · " + esc(z.temp_f) + "°F" : ""), { sticky: true }).addTo(zoneLayer);
      var b = z["break"];
      if (b) {
        L.circleMarker([b.lat, b.lng], { radius: 6, color: "#ffffff", weight: 2, fill: false })
          .bindTooltip("Break " + esc(b.step_f) + "°F over " + esc(b.over_nm) + " nm · " + esc(b.gps), { direction: "top" }).addTo(zoneLayer);
      }
    });
    (M.stops || []).forEach(function (s) {
      var age = Math.round((Date.parse(facts.date) - Date.parse(s.date)) / 864e5);
      L.circleMarker([s.lat, s.lng], { radius: 4, color: "#0b1220", weight: 1, fillColor: AGE_COLORS[Math.min(age, 3)], fillOpacity: 1 })
        .bindTooltip("Fleet stop " + esc(md(s.date)) + ", " + esc(Math.round(s.min)) + " min", { direction: "top" }).addTo(fleetLayer);
    });
    gpsMarkers = L.layerGroup().addTo(map);
    (((facts.fishing || {}).fleet_now || {}).at_sea || []).forEach(function (bt) {
      if (bt.lat == null) return;
      L.circleMarker([bt.lat, bt.lng], { radius: 5, color: "#0b1220", weight: 1.5, fillColor: "#fbbf24", fillOpacity: 1 })
        .bindTooltip(esc(bt.name) + ": " + esc(bt.stopped ? "stopped" : ["under way", bt.sog, "kt"].join(" ")) + ", " + esc(localTime(bt.t)), { direction: "top" }).addTo(liveLayer);
    });
    ((facts.fishing || {}).spots || []).forEach(function (s) {
      L.circleMarker([s.lat, s.lng], { radius: 2.5, color: "#e2e8f0", weight: 1, fillOpacity: 1 })
        .bindTooltip(esc(s.name) + (s.sst_f != null ? " · " + esc(s.sst_f) + "°F" : ""), { direction: "right" }).addTo(spotLayer);
    });
    L.control.layers(null, { "Water temperature": sstLayer, "Predicted visibility": visLayer, "Zones and breaks": zoneLayer, "Paddies": padLayer,
      "Fleet stops, 3 days": fleetLayer, "Boats now": liveLayer, "Named spots": spotLayer }, { position: "topright", collapsed: true }).addTo(map);
  }

  function weather() {
    var w = facts.weather || {};
    var times = [srcTime("cwf_sgx"), srcTime("cwf_lox")];
    var syn = (w.synopsis || []).map(function (s, i) {
      if (!s.text) return "";
      return '<div class="syn"><div class="mut small">' + esc(s.office) + (times[i] ? ", issued " + esc(times[i]) : "") + "</div><blockquote>" + esc(s.text) + "</blockquote></div>";
    }).join("");
    var heads = (w.headlines || []).length ? '<ul class="plain heads">' + w.headlines.map(function (h) {
      return "<li><b>" + esc(h.event) + "</b>" + (h.until ? " until " + esc(h.until) : "") + '<div class="mut small">' + esc(h.where || h.area) + "</div>" +
        (h.description ? '<details class="raw"><summary>What NWS says</summary><p>' + esc(h.description) + "</p></details>" : "") + "</li>";
    }).join("") + "</ul>" : '<p class="small mut">No NWS marine or beach headlines in effect.</p>';
    var days = ((w.outlook || [])[0] || {}).days || [];
    var grid = (w.outlook || []).length ? '<div class="scroll"><table class="outlook"><thead><tr><th>Zone</th>' + days.map(function (d) { return "<th>" + esc(d.label) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + w.outlook.map(function (row) {
        return "<tr><td>" + esc(row.zone) + "</td>" + row.days.map(function (d) {
          var a = d.wind_max_kt == null ? 0 : Math.min(1, d.wind_max_kt / 25);
          return '<td style="background:rgba(57,135,229,' + (0.06 + a * 0.4).toFixed(2) + ')"><b>' + esc([d.wind_dir || "", d.wind_max_kt != null ? d.wind_max_kt + " kt" : ""].join(" ")) +
            '</b><div class="small">' + esc(d.hs_max_ft != null ? [d.hs_max_ft, "ft", d.tp_s ? ["at", d.tp_s, "s"].join(" ") : ""].join(" ") : "") + "</div></td>";
        }).join("") + "</tr>";
      }).join("") + '</tbody></table></div><p class="note">Daytime peak wind (darker as it rises) and waves from our hourly model at the middle of each zone; the NWS forecasts above cover whole zones.</p>' : "";
    var now = (w.wind_now || []).map(function (b) { return esc(b.name) + " " + esc(b.kt) + " kt" + (b.gust_kt ? " (gusts " + esc(b.gust_kt) + ")" : "") + " at " + esc(b.observed); }).join("; ");
    return '<section><h2 id="weather">Weather</h2>' + syn + "<h3>Advisories</h3>" + heads + "<h3>Next three days</h3>" + grid +
      (now ? '<p class="small">Wind at the buoys now: ' + now + ".</p>" : "") + "</section>";
  }

  function zoneCard(z) {
    var f = z.fleet || {}, b = z["break"], p = z.paddy;
    var temps = (z.temp_series || []).map(function (s) { return s.temp_f; });
    var series = f.series || [];
    var gps = [];
    if (b) gps.push({ label: "Break", text: b.gps, lat: b.lat, lng: b.lng, tip: ["Break", b.step_f + "°F over", b.over_nm, "nm"].join(" ") });
    if (p) gps.push({ label: ["Paddies", "(" + String(p.word) + ")"].join(" "), text: p.gps, lat: p.lat, lng: p.lng, tip: ["Paddy region", "(" + String(p.word) + ")"].join(" ") });
    return '<article class="zone" id="z-' + esc(z.id) + '"><header><h3>' + esc(z.name) + '</h3><span class="mut small">' + esc(z.nm_from_sd) + " nm</span></header>" +
      '<div class="mut small spots">' + esc(z.spots) + "</div>" +
      (z.notice ? '<div class="notice small">' + (z.notice.url ? link(z.notice.url, z.notice.text) : esc(z.notice.text)) + "</div>" : "") +
      '<div class="row"><div class="metric"><span class="k">Water</span><span class="v">' + (z.temp_f != null ? esc(z.temp_f) + "°F" : "n/a") + "</span>" +
      (z.temp_change_f != null ? '<span class="d">' + esc(signed(z.temp_change_f, "°F")) + " in " + esc(z.temp_days) + " days</span>" : "") + "</div>" + spark(temps, C.s1) + "</div>" +
      '<dl class="facts">' +
      "<dt>Break</dt><dd>" + (b ? esc([b.step_f + "°F over", b.over_nm, "nm, warm side", b.warm_side].join(" ")) : '<span class="mut">no break in the zone</span>') + "</dd>" +
      "<dt>Color</dt><dd>" + (z.chl != null ? esc([z.chl, "mg/m³ (" + colorWord(z.chl) + ")"].join(" ")) : '<span class="mut">cloud, no reading</span>') + "</dd>" +
      "<dt>Paddies</dt><dd>" + (p ? esc(p.word) + '<span class="mut"> · drift model</span>' : '<span class="mut">none in the model</span>') + "</dd>" +
      (z.vis_pred ? "<dt>Visibility</dt><dd>" + esc(["about", z.vis_pred.ft, "ft", z.vis_pred.lo_ft != null ? "(" + [z.vis_pred.lo_ft, z.vis_pred.hi_ft].join("-") + ")" : ""].join(" ")) +
        '<span class="mut"> · predicted</span></dd>' : "") +
      "<dt>Fleet (AIS)</dt><dd>" + esc([f.last3_hours || 0, "h last 3 days,", f.prior7_hours || 0, "h the 7 before"].join(" ")) +
      (series.some(function (s) { return s.hours > 0; }) ? bars(series.map(function (s) { return s.hours; }), C.s2, 112, 22, "Fleet hours stopped per day, last 14 days") : "") +
      '<div class="mut small">Same weeks last year: ' + esc(f.last_year_hours || 0) + " h" + (f.boats_last3 && f.boats_last3.length ? " · recent: " + esc(f.boats_last3.join(", ")) : "") + "</div></dd>" +
      (z.water_fits && z.water_fits.length ? "<dt>Water fits</dt><dd>" + z.water_fits.map(function (s) { return '<span class="pill">' + esc(s) + "</span>"; }).join("") + "</dd>" : "") +
      "<dt>Next days</dt><dd>" + (z.outlook || []).map(function (d) {
        return esc([d.label, d.wind_dir || "", d.wind_max_kt != null ? d.wind_max_kt + " kt," : "", d.hs_max_ft != null ? d.hs_max_ft + " ft" : ""].join(" "));
      }).join(" · ") + "</dd>" +
      (gps.length ? "<dt>GPS</dt><dd>" + gps.map(function (g) {
        return '<div class="gps"><span class="mut small">' + esc(g.label) + '</span> <b>' + esc(g.text) + '</b> <button type="button" class="mini-btn" data-copy="' + esc(g.text) +
          '">Copy</button><button type="button" class="mini-btn" data-lat="' + esc(g.lat) + '" data-lng="' + esc(g.lng) + '" data-tip="' + esc(g.tip + " · " + g.text) + '">Map</button></div>';
      }).join("") + "</dd>" : "") +
      "</dl></article>";
  }

  function jumpNav() {
    var zs = facts.zones || [];
    return '<nav class="jump" aria-label="Jump to a zone">' + (facts.zone_groups || []).map(function (g) {
      return zs.filter(function (z) { return z.group === g.key; }).map(function (z) {
        return '<a href="#z-' + esc(z.id) + '">' + esc(z.short || z.name) + "</a>";
      }).join("");
    }).join("") + "</nav>";
  }

  function bindGps() {
    Array.prototype.forEach.call(main.querySelectorAll("button[data-copy]"), function (btn) {
      btn.addEventListener("click", function () {
        var t = btn.getAttribute("data-copy");
        var done = function () { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1500); };
        if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, function () { /* clipboard blocked */ });
      });
    });
    Array.prototype.forEach.call(main.querySelectorAll("button[data-lat]"), function (btn) {
      btn.addEventListener("click", function () {
        if (!map || !gpsMarkers) return;
        var ll = [parseFloat(btn.getAttribute("data-lat")), parseFloat(btn.getAttribute("data-lng"))];
        gpsMarkers.clearLayers();
        L.circleMarker(ll, { radius: 9, color: "#fbbf24", weight: 3, fill: false }).bindTooltip(esc(btn.getAttribute("data-tip")), { permanent: true, direction: "top" }).addTo(gpsMarkers);
        map.setView(ll, 10, { animate: false });
        document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
      });
    });
  }

  function zonesSection(groups) {
    var zs = facts.zones || [];
    return '<section><h2 id="zones">Zones</h2>' + (facts.zone_groups || []).filter(function (g) { return groups.indexOf(g.key) >= 0; }).map(function (g) {
      return "<h3>" + esc(g.label) + '</h3><div class="zones">' + zs.filter(function (z) { return z.group === g.key; }).map(zoneCard).join("") + "</div>";
    }).join("") + '<p class="note">Water and breaks from the satellite map, color from chlorophyll, fleet from AIS (heard within about 30-50 nm of shore; most boats under 65 ft never broadcast). ' +
      '"Water fits" lists species whose usual range includes the zone\'s water this month. ' + esc((facts.fishing || {}).counts_note || "") + "</p></section>";
  }

  function diveSites() {
    var sites = facts.dive_sites || [];
    if (!sites.length) return "";
    return '<section><h2 id="sites">Dive sites</h2><div class="zones">' + sites.map(function (s) {
      var vis = s.vis || [];
      var lastVis = vis.length ? vis[vis.length - 1] : null;
      var temps = (s.temp_series || []).map(function (t) { return t.temp_f; });
      var vp = s.vis_pred;
      return '<article class="zone"><header><h3>' + esc(s.name) + '</h3></header><div class="mut small spots">' + esc(s.note) + "</div>" +
        (vp ? '<div class="row"><div class="metric"><span class="k">Visibility, predicted</span><span class="v">' + esc(vp.ft) + " ft</span>" +
          (vp.lo_ft != null ? '<span class="d">' + esc([vp.lo_ft, "to", vp.hi_ft, "ft likely"].join(" ")) + "</span>" : "") + "</div>" +
          (lastVis ? '<div class="metric right"><span class="k">Last shop post</span><span class="v2">' + esc([lastVis.lo_ft === lastVis.hi_ft ? lastVis.lo_ft : [lastVis.lo_ft, lastVis.hi_ft].join("-"), "ft"].join(" ")) +
            '</span><span class="d">' + esc(md(lastVis.date)) + "</span></div>" : "") + "</div>" : "") +
        '<div class="row"><div class="metric"><span class="k">Water, buoy ' + esc(s.buoy) + '</span><span class="v">' + (temps.length ? esc(temps[temps.length - 1]) + "°F" : "n/a") + "</span>" +
        (temps.length > 7 ? '<span class="d">' + esc(signed(temps[temps.length - 1] - temps[temps.length - 8], "°F")) + " in 7 days</span>" : "") + "</div>" + spark(temps, C.s1) + "</div>" +
        '<dl class="facts"><dt>Sets at the entry</dt><dd>' + (s.surf || []).map(function (d) {
          return esc([d.label, d.sets_ft < 1 ? "under 1" : d.sets_ft.toFixed(0), "ft at", d.tp_s, "s"].join(" "));
        }).join(" · ") + '<div class="mut small">CDIP nearshore wave model at the 10 m line</div></dd>' +
        (vp ? "" : "<dt>Visibility</dt><dd>" + (lastVis ? esc([lastVis.lo_ft === lastVis.hi_ft ? lastVis.lo_ft : lastVis.lo_ft + "-" + lastVis.hi_ft, "ft on", md(lastVis.date)].join(" ")) +
          '<div class="mut small">' + esc(s.vis_source) + " posts</div>" : '<span class="mut">no shop posts for this site</span>') + "</dd>") +
        (s.surge ? "<dt>Surge at " + esc(s.surge.depth_ft) + " ft</dt><dd>" + esc([s.surge.u_ft_s, "ft/s bottom water speed", "(" + String(s.surge.level) + ")"].join(" ")) + "</dd>" : "") +
        "</dl></article>";
    }).join("") + "</div></section>";
  }

  function around(spearOnly) {
    var rows = ((facts.fishing || {}).inshore || []).filter(function (r) { return !spearOnly || r.spear; });
    if (!rows.length) return "";
    var season = (facts.divers || {}).season || [];
    return '<section><h2 id="around">' + (spearOnly ? "What's around" : "Inshore species") + '</h2><ul class="plain">' + rows.map(function (r) {
      return "<li><b>" + esc(r.label) + "</b> " + tagHtml(r.status) + '<div class="small">' + esc(r.today || r.drivers) + '</div><div class="mut small">' + esc(r.look) + "</div></li>";
    }).join("") + "</ul>" + (season.length ? '<h3>Seasons</h3><ul class="plain">' + season.map(function (s) {
      return "<li><b>" + esc(s.label) + ":</b> " + esc(s.text) + ' <span class="mut small">(' + link(s.source, "CDFW") + ")</span></li>";
    }).join("") + "</ul>" : "") + "</section>";
  }

  function trendsSection() {
    var t = facts.trends || {};
    var buoys = t.buoys || [];
    var cols = [C.s1, C.s2, C.s3, C.s4];
    function bseries(ids, key) {
      return ids.map(function (id, i) {
        var b = buoys.filter(function (x) { return x.id === id; })[0];
        if (!b) return null;
        return { name: b.name, short: BUOY_SHORT[id] || b.name, color: cols[i], points: b.days.map(function (d) { return { d: d.date, v: d[key] != null ? d[key] : null }; }) };
      }).filter(Boolean);
    }
    var out = '<section><h2 id="trends">Trends</h2><div class="charts">';
    out += lineChart({ title: "Water temperature at the buoys", sub: "daily mean, °F", unit: "°F", decimals: 0,
      series: bseries(view === "fishing" ? ["46254", "46232", "46086", "46047"] : ["46254", "46232", "46253"], "temp_f") });
    var cal = (facts.visibility || {}).calibration || {};
    if (view !== "fishing" && cal.pairs && cal.pairs.length > 5) {
      out += lineChart({ title: "Predicted vs posted visibility, La Jolla", sub: [cal.n, "shop reports; prediction scaled by", cal.scale].join(" "), unit: " ft",
        series: [{ name: "Shop post (range)", short: "Posted", color: C.s1, range: true, points: cal.pairs.map(function (p) { return { d: p.date, lo: p.obs_lo, hi: p.obs_hi }; }) },
                 { name: "Predicted", short: "Predicted", color: C.s2, points: cal.pairs.map(function (p) { return { d: p.date, v: p.pred_ft }; }) }] });
    }
    var vis = (t.vis || {})["dive-shop-justgetwet"];
    if (view !== "fishing" && vis && vis.length > 2) {
      out += lineChart({ title: "Visibility posted by Just Get Wet (La Jolla)", sub: "low to high of each post, ft", unit: " ft",
        refs: view === "spearfishing" ? [{ v: 15, label: "15 ft" }] : [],
        series: [{ name: "Visibility", color: C.s1, range: true, points: vis.map(function (p) { return { d: p.date, lo: p.lo_ft, hi: p.hi_ft }; }) }] });
    }
    var gl = (t.glider || []).filter(function (g) { return g.mixed_layer_ft != null; });
    if (gl.length > 2) {
      out += lineChart({ title: "Depth of the warm top layer offshore", sub: "Spray glider nearest San Diego each day, ft, deeper is lower", unit: " ft", invert: true,
        series: [{ name: "Warm layer", color: C.s3, points: gl.map(function (g) { return { d: g.date, v: g.mixed_layer_ft }; }) }] });
    }
    out += lineChart({ title: "Wave height at the buoys", sub: "daily mean, ft", unit: " ft", decimals: 1, series: bseries(["46232", "46086"], "wave_ft") });
    if (view === "fishing" && (t.enso || []).length > 3) {
      out += lineChart({ title: "El Niño: weekly Niño-3.4 anomaly", sub: "NOAA CPC, °C", unit: "°C", decimals: 1, zero: true,
        series: [{ name: "Niño-3.4", color: C.s2, points: t.enso.map(function (e) { return { d: e.date, v: e.nino34 }; }) }] });
    }
    return out + "</div>" + (t.archive_days ? '<p class="note">Our daily zone archive has ' + esc(t.archive_days) + " day(s) so far; zone trends lengthen as it grows.</p>" : "") + "</section>";
  }

  function fleetSection() {
    var now = (facts.fishing || {}).fleet_now;
    if (!now || !now.at_sea || !now.at_sea.length) return "";
    return '<section><h2 id="fleet">Boats out now</h2><ul class="plain cols">' + now.at_sea.map(function (b) {
      return "<li><b>" + esc(b.name) + "</b> " + esc(b.stopped ? "stopped" : ["under way", b.sog, "kt"].join(" ")) + (b.spot ? " near " + esc(b.spot) : "") +
        ' <span class="mut small">' + esc(b.nm_from_sd) + " nm · " + esc(localTime(b.t)) + "</span></li>";
    }).join("") + '</ul><p class="note">AIS positions under ' + esc(now.max_age_h) + " hours old. " + link("/fleet/#view=live", "Live fleet map") + ".</p></section>";
  }

  function bait() {
    var b = facts.bait;
    if (!b) return "";
    return '<section><h2 id="bait">Bait</h2><p class="small">' + esc(b.text) + " " + b.links.map(function (l) { return link(l.url, l.name); }).join("; ") + ".</p></section>";
  }

  function spotsTable() {
    var spots = (facts.fishing || {}).spots || [];
    if (!spots.length) return "";
    return '<section><details class="raw"><summary><b>Named spots</b>: water, nearest break, color</summary><div class="scroll"><table><thead><tr><th>Spot</th><th class="n">nm</th><th class="n">Water</th><th>Nearest break</th><th class="n">Chl</th></tr></thead><tbody>' +
      spots.slice().sort(function (a, b) { return a.nm_from_sd - b.nm_from_sd; }).map(function (s) {
        var b = s["break"];
        return "<tr><td>" + esc(s.name) + '</td><td class="n">' + esc(s.nm_from_sd) + '</td><td class="n">' + esc(s.sst_f != null ? s.sst_f + "°F" : "") + "</td><td>" +
          (b ? esc([b.step_f + "°F over", b.over_nm, "nm,", b.direction ? [b.nm, "nm", b.direction].join(" ") : "here"].join(" ")) : '<span class="mut">none within 10 nm</span>') +
          '</td><td class="n">' + esc(s.chl != null ? s.chl : "") + "</td></tr>";
      }).join("") + "</tbody></table></div></details></section>";
  }

  function basics() {
    var w = facts.water || {};
    var tide = ((w.tide || {}).events || []).map(function (e) { return esc([e.type, e.ft, "ft at", e.time].join(" ")); }).join(", ");
    var o = (w.thermocline || {}).observed;
    return '<section><h2 id="basics">Tide, sun, moon</h2><dl class="kv">' + (tide ? "<dt>La Jolla tide</dt><dd>" + tide + "</dd>" : "") +
      (w.sun ? "<dt>Sun</dt><dd>Up " + esc(w.sun.rise) + ", down " + esc(w.sun.set) + "</dd>" : "") +
      (w.moon ? "<dt>Moon</dt><dd>" + esc([w.moon.phase + ",", w.moon.illuminated_pct + "% lit"].join(" ")) + "</dd>" : "") +
      (o ? "<dt>Latest glider profile</dt><dd>" + esc([o.surface_f + "°F surface,", o.t60_f + "°F at 60 ft,", o.t100_f + "°F at 100 ft (" + o.glider + ",", o.nm_from_sd, "nm out,", o.time + ")"].join(" ")) + "</dd>" : "") +
      "</dl></section>";
  }

  function sourcesBlock() {
    var gaps = facts.not_covered || [];
    var ok = facts.sources.filter(function (s) { return s.status === "ok"; }).length;
    return '<section><details class="raw"><summary><b>Sources</b> (' + ok + " of " + facts.sources.length + " live) and gaps</summary>" +
      '<div class="scroll"><table class="mini"><tbody>' + facts.sources.map(function (s) {
        return "<tr><td>" + link(s.url, s.label) + '</td><td class="status-' + esc(s.status) + '">' + esc(s.status) + "</td><td>" + esc(localTime(s.time_utc)) + "</td></tr>";
      }).join("") + "</tbody></table></div>" + (gaps.length ? '<p class="small"><b>Not covered:</b></p><ul class="plain small">' + gaps.map(function (g) { return "<li>" + esc(g) + "</li>"; }).join("") + "</ul>" : "") +
      "</details></section>";
  }

  function body() {
    if (view === "diving") return mapSection() + diveSites() + trendsSection() + weather() + basics() + sourcesBlock();
    if (view === "spearfishing") return diveSites() + around(true) + trendsSection() + mapSection() + weather() + basics() + sourcesBlock();
    return jumpNav() + mapSection() + weather() + zonesSection(["offshore", "mexico", "inshore"]) + fleetSection() + trendsSection() + bait() + around(false) + spotsTable() + sourcesBlock();
  }

  function render() {
    CHARTS = {}; chartSeq = 0;
    var tabs = '<div class="tabs" role="tablist" aria-label="Report view">' + VIEWS.map(function (v) {
      return '<button type="button" role="tab" aria-selected="' + (v[0] === view) + '" data-view="' + v[0] + '" class="' + (v[0] === view ? "on" : "") + '">' + v[1] + "</button>";
    }).join("") + "</div>";
    main.innerHTML = '<h1>Ocean Report</h1><p class="mut">' + esc(facts.date_label) + " · updated " + esc(facts.generated_local) + "</p>" + tabs + highlights() + body();
    Array.prototype.forEach.call(main.querySelectorAll(".tabs button"), function (b) {
      b.addEventListener("click", function () {
        view = b.getAttribute("data-view");
        try { localStorage.setItem("sd:report:view", view); } catch (e) { /* storage blocked */ }
        history.replaceState(null, "", "#view=" + view);
        var y = window.scrollY; render(); window.scrollTo(0, y);
      });
    });
    bindCharts();
    initMap();
    bindGps();
  }

  function load(date) {
    return fetch(DATA + (date ? date : "latest") + ".json", { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function unavailable() {
    main.innerHTML = '<h1>Ocean Report</h1><p>The report is in preview and is not published on this site yet.</p><p><a href="/">Back to the map</a></p>';
  }

  var params = new URLSearchParams(location.search);
  var want = /^\d{4}-\d{2}-\d{2}$/.test(params.get("date") || "") ? params.get("date") : null;
  fetch(DATA + "index.json", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : { dates: [] }; })
    .catch(function () { return { dates: [] }; })
    .then(function (idx) {
      (idx.dates || []).forEach(function (d) {
        var o = document.createElement("option");
        o.value = d; o.textContent = d;
        if (d === (want || idx.latest)) o.selected = true;
        picker.appendChild(o);
      });
      picker.addEventListener("change", function () {
        var u = new URL(location.href);
        u.searchParams.set("date", picker.value);
        location.href = u.toString();
      });
      return load(want);
    })
    .then(function (f) { if (!f.zones) throw new Error("older report format"); facts = f; render(); })
    .catch(unavailable);
})();
