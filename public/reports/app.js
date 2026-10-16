/* Ocean Report: what the water, the weather and the fleet are doing, and how
   it moved. Renders /data/reports/<date>.json (pipeline/reports/build_report.py).
   Every string from the data passes through esc(); the page adds no numbers. */
(function () {
  "use strict";
  var DATA = "/data/reports/";
  var VIEWS = [["fishing", "Fishing"], ["diving", "Diving and spearfishing"]];
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
    // Spearfishing had its own tab until 2026-10-15; old links and saved choices land on diving.
    var m = /view=(fishing|diving|spearfishing)/.exec(location.hash);
    if (m) return m[1] === "fishing" ? "fishing" : "diving";
    try { var v = localStorage.getItem("sd:report:view"); if (v) return v === "fishing" ? "fishing" : "diving"; } catch (e) { /* storage blocked */ }
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
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
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
      return '<span class="key"><i class="' + (s.band ? "band" : "") + '" style="background:' + esc(s.color) + '"></i>' + esc(s.name) + "</span>";
    }).join("") + "</div>" : "";
    var table = '<details class="data"><summary>Data</summary><div class="scroll"><table class="mini"><thead><tr><th>Date</th>' +
      spec.series.map(function (s) { return "<th>" + esc(s.short || s.name) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      dates.map(function (d) {
        return "<tr><td>" + esc((spec.tipPrefix || "") + md(d)) + "</td>" + spec.series.map(function (s) {
          var p = s.points.filter(function (q) { return q.d === d; })[0];
          return "<td>" + (p && (p.v != null || p.lo != null) ? esc(p.v != null ? p.v : [p.lo, p.hi].join("-")) : "") + "</td>";
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
    var d0 = dayNum(spec.domain ? spec.domain[0] : spec.dates[0]), d1 = dayNum(spec.domain ? spec.domain[1] : spec.dates[spec.dates.length - 1]);
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
    if (spec.domain) {
      var yr = spec.domain[0].slice(0, 4), every = W < 520 ? 3 : 1;
      for (var mo = 0; mo < 12; mo += every) {
        var md0 = yr + "-" + (mo < 9 ? "0" : "") + (mo + 1) + "-01", mx = X(md0);
        g += '<line x1="' + mx.toFixed(1) + '" x2="' + mx.toFixed(1) + '" y1="' + (H - B) + '" y2="' + (H - B + 4) + '" stroke="#334155"/>' +
          '<text x="' + (mx + 2).toFixed(1) + '" y="' + (H - 5) + '" class="ax">' + esc(MONTHS[mo]) + "</text>";
      }
      if (spec.nowDate) {
        var nx = X(spec.nowDate);
        g += '<line x1="' + nx.toFixed(1) + '" x2="' + nx.toFixed(1) + '" y1="' + T + '" y2="' + (H - B) + '" stroke="#475569" stroke-dasharray="2 3"/>';
      }
    } else {
      g += '<text x="' + L + '" y="' + (H - 5) + '" class="ax">' + esc(md(spec.dates[0])) + '</text><text x="' + (W - R) + '" y="' + (H - 5) +
        '" text-anchor="end" class="ax">' + esc(md(spec.dates[spec.dates.length - 1])) + "</text>";
    }
    var ends = [];
    spec.series.forEach(function (s) {
      if (s.band) {
        var seg = [], segs = [];
        s.points.forEach(function (p) { if (p.lo != null && p.hi != null) seg.push(p); else if (seg.length) { segs.push(seg); seg = []; } });
        if (seg.length) segs.push(seg);
        segs.forEach(function (q) {
          if (q.length < 2) return;
          var top = q.map(function (p, k) { return (k ? "L" : "M") + X(p.d).toFixed(1) + " " + Y(p.hi).toFixed(1); }).join(" ");
          var bot = q.slice().reverse().map(function (p) { return "L" + X(p.d).toFixed(1) + " " + Y(p.lo).toFixed(1); }).join(" ");
          g += '<path d="' + top + " " + bot + ' Z" fill="' + esc(s.color) + '" fill-opacity="0.22" stroke="none"/>';
        });
        return;
      }
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
      tip.innerHTML = '<div class="mut">' + esc((spec.tipPrefix || "") + md(dsel)) + "</div>" + spec.series.map(function (s) {
        var p = s.points.filter(function (q) { return q.d === dsel; })[0];
        if (!p || (p.v == null && p.lo == null)) return "";
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

  // --- Field reports (approved, from /api/reports/feed) --------------------------
  // Lists mirror functions/api/reports/_lib.js; the server rejects anything else.
  var OBS_SPECIES = ["yellowfin", "bluefin", "skipjack", "dorado", "yellowtail", "wahoo", "striped marlin", "white seabass",
    "calico bass", "sand bass", "halibut", "sheephead", "bonito", "barracuda", "rockfish", "lingcod", "whitefish", "sculpin", "lobster", "mako", "other"];
  var OBS_METHODS = ["fly-line", "surface iron", "yo-yo iron", "flat-fall", "kite", "slow-troll", "trolling", "dropper loop", "sinker rig", "popper", "swimbait",
    "bottom fishing", "spear", "jig"];
  var OBS_SIGHTINGS = ["kelp paddy", "birds working", "foamers or breezers", "porpoise", "whales", "bait ball",
    "marlin (tailer or sleeper)", "color or temperature break", "current line", "red tide"];
  var OBS_BAITS = ["sardine", "anchovy", "mackerel", "squid", "mixed", "none"];
  var OBS_BARGES = ["San Diego Bay", "Mission Bay", "Oceanside", "Dana Point", "Newport Beach", "San Pedro / LA Harbor",
    "Redondo / King Harbor", "Marina del Rey", "Ensenada"];
  var OBS_COLORS = ["blue", "blue-green", "green", "off-color", "red tide"];
  var OBS_TYPES = [["catch", "Catch"], ["sighting", "Sighting"], ["bait", "Bait at the barge"], ["dive", "Dive"]];
  var OBS_DAYS = 7;
  // Report pins: fuchsia diamonds (a hue nothing else on the map uses), fading with age (today, 1-2 days, 3-6 days).
  var OBS_AGE_OPACITY = [1, 0.75, 0.45];
  var OBS = [], pickMode = false, pickMarker = null;

  function nm(a, b) {
    var R = 3440.065, toR = Math.PI / 180;
    var dLat = (b[0] - a[0]) * toR, dLng = (b[1] - a[1]) * toR;
    var h = Math.pow(Math.sin(dLat / 2), 2) + Math.cos(a[0] * toR) * Math.cos(b[0] * toR) * Math.pow(Math.sin(dLng / 2), 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  // Same rule as pipeline/reports/zones.py zone_of: inside a circle, relatively nearest center.
  function zoneOf(lat, lng) {
    var best = null;
    (facts.zones || []).forEach(function (z) {
      var d = nm([lat, lng], z.center), k = d / z.radius_nm;
      if (d <= z.radius_nm && (!best || k < best.k)) best = { k: k, z: z };
    });
    return best ? best.z : null;
  }
  function ddmPart(v) {
    v = Math.abs(v);
    var d = Math.floor(v), m = Math.round((v - d) * 60);
    if (m === 60) { d += 1; m = 0; }
    return d + " " + (m < 10 ? "0" : "") + m;
  }
  function ddm(lat, lng) { return ddmPart(lat) + " x " + ddmPart(lng); }
  function obsAge(o) { return Math.round((dayNum(facts.date) - dayNum(o.date))); }
  function obsRecent(types) {
    return OBS.filter(function (o) {
      var a = obsAge(o);
      return a >= 0 && a < OBS_DAYS && (!types || types.indexOf(o.type) >= 0);
    }).sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
  }
  function visRange(o) { return o.vis_lo_ft === o.vis_hi_ft ? String(o.vis_lo_ft) : [o.vis_lo_ft, o.vis_hi_ft].join("-"); }
  function obsWhat(o) {
    if (o.type === "catch") {
      var cnt = o.counts || {};
      return (o.species || []).map(function (sp) { return (cnt[sp] ? cnt[sp] + " " : "") + sp; }).join(", ") +
        (o.bait && o.bait !== "none" ? " on " + o.bait : "") + (o.method ? " (" + o.method + ")" : "");
    }
    if (o.type === "sighting") return o.sighting + ((o.species || []).length ? " (" + o.species.join(", ") + ")" : "");
    if (o.type === "bait") return [o.bait, o.bait_size || "", "at", o.barge].join(" ").replace(/\s+/g, " ");
    if (o.type === "dive") return [visRange(o), "ft visibility", o.depth_ft ? "at " + o.depth_ft + " ft" : "", o.surge ? "(" + o.surge + " surge)" : ""].join(" ").replace(/\s+/g, " ").trim();
    return "";
  }
  function obsExtras(o) {
    return [o.water_temp_f != null ? o.water_temp_f + "°F" : "", o.color ? o.color + " water" : ""].filter(Boolean).join(", ");
  }
  function obsItem(o) {
    var g = ddm(o.lat, o.lng);
    return "<li><b>" + esc(obsWhat(o)) + "</b>" + (o.confidence === "confirmed" ? tagHtml("confirmed") : "") +
      '<div class="mut small">' + esc([md(o.date), obsExtras(o)].filter(Boolean).join(" · ")) +
      (o.type !== "bait" ? ' · <span class="gps">' + esc(g) + '</span><button type="button" class="mini-btn" data-copy="' + esc(g) +
        '">Copy</button><button type="button" class="mini-btn" data-lat="' + esc(o.lat) + '" data-lng="' + esc(o.lng) + '" data-tip="' + esc(obsWhat(o) + " · " + g) + '">Map</button>' : "") +
      "</div>" + (o.notes ? '<div class="small quote">' + esc(o.notes) + "</div>" : "") + "</li>";
  }
  function sendLink(text) { return '<a href="#send" class="send-open">' + esc(text) + "</a>"; }

  function waterSection() {
    var types = ["catch", "sighting", "dive"];
    var recent = obsRecent(types);
    var groups = [], seen = {};
    recent.forEach(function (o) {
      var z = zoneOf(o.lat, o.lng), key = z ? z.id : "_other";
      if (!seen[key]) { seen[key] = { label: z ? z.name : "Other waters", order: z ? (facts.zones || []).indexOf(z) : 999, rows: [] }; groups.push(seen[key]); }
      seen[key].rows.push(o);
    });
    groups.sort(function (a, b) { return a.order - b.order; });
    var list = groups.length ? groups.map(function (g) {
      return "<h3>" + esc(g.label) + '</h3><ul class="plain obs">' + g.rows.map(obsItem).join("") + "</ul>";
    }).join("") : '<p class="small mut">No reports from the water in the last ' + OBS_DAYS + " days. " + sendLink("Send the first one") + ".</p>";
    return '<section><h2 id="water">From the water</h2>' + reportForm() + list +
      '<p class="note">Sent in by people on the water and approved by a moderator before they show. Positions are rounded to about a mile. "Confirmed" means two different people reported the same thing nearby within a few days, or the reporter has a track record.</p></section>';
  }

  function reportForm() {
    var def = view === "fishing" ? "catch" : "dive";
    function opts(list, blank) { return (blank ? '<option value="">' + esc(blank) + "</option>" : "") + list.map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + "</option>"; }).join(""); }
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem("sd:obs:who") || "{}") || {}; } catch (e) { /* storage blocked */ }
    var today = new Date().toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" });
    return '<details class="raw send" id="send"><summary><b>Send a report</b>: what you caught, saw or dove</summary>' +
      '<div class="say"><label for="obs-say" class="small">Say it in a sentence (or use your phone\'s mic):</label>' +
      '<textarea id="obs-say" rows="2" maxlength="1000" placeholder="12 yellowtail at the 9 Mile on fly-lined sardines, 70 degrees"></textarea>' +
      '<div class="send-row"><button type="button" class="mini-btn" id="obs-read">Fill in the form</button><span id="obs-say-status" class="small mut" role="status" aria-live="polite"></span></div></div>' +
      '<form id="obs-form" novalidate><fieldset class="types"><legend class="mut small">What kind</legend>' + OBS_TYPES.map(function (t) {
        return '<label><input type="radio" name="type" value="' + t[0] + '"' + (t[0] === def ? " checked" : "") + "> " + esc(t[1]) + "</label>";
      }).join("") + "</fieldset>" +
      '<div class="fgrid">' +
      '<label>Date <input type="date" name="date" value="' + esc(today) + '" max="' + esc(today) + '" required></label>' +
      '<label data-for="catch sighting dive" class="wide">Where (GPS) <input type="text" name="gps" inputmode="decimal" autocomplete="off" placeholder="32 52 x 117 17, or 32.87, -117.28">' +
      '<span class="btns"><button type="button" class="mini-btn" id="obs-pick">Pick on the map</button><button type="button" class="mini-btn" id="obs-here">Where I am</button></span></label>' +
      '<label data-for="catch">Species <select name="species">' + opts(OBS_SPECIES, "Pick one") + "</select></label>" +
      '<label data-for="catch">Also caught <select name="species2">' + opts(OBS_SPECIES, "(none)") + "</select></label>" +
      '<label data-for="catch">How many <input type="number" name="count" min="1" max="2000" inputmode="numeric" placeholder="first species"></label>' +
      '<label data-for="catch">Bait used <select name="bait_used">' + opts(OBS_BAITS, "(not sure)") + "</select></label>" +
      '<label data-for="catch">Method <select name="method">' + opts(OBS_METHODS, "(skip)") + "</select></label>" +
      '<label data-for="catch">Biggest (lb) <input type="number" name="size_lb" min="0" max="1000" step="0.5" inputmode="decimal"></label>' +
      '<label data-for="sighting">What you saw <select name="sighting">' + opts(OBS_SIGHTINGS, "Pick one") + "</select></label>" +
      '<label data-for="sighting">Fish with it <select name="species_seen">' + opts(OBS_SPECIES, "(none)") + "</select></label>" +
      '<label data-for="bait">Barge <select name="barge">' + opts(OBS_BARGES, "Pick one") + "</select></label>" +
      '<label data-for="bait">Bait <select name="bait">' + opts(OBS_BAITS, "Pick one") + "</select></label>" +
      '<label data-for="bait">Size or quality <input type="text" name="bait_size" maxlength="20" placeholder="small, frisky"></label>' +
      '<label data-for="dive">Visibility, low (ft) <input type="number" name="vis_lo_ft" min="0" max="150" inputmode="numeric"></label>' +
      '<label data-for="dive">Visibility, high (ft) <input type="number" name="vis_hi_ft" min="0" max="150" inputmode="numeric"></label>' +
      '<label data-for="dive">Depth (ft) <input type="number" name="depth_ft" min="0" max="200" inputmode="numeric"></label>' +
      '<label data-for="dive">Surge <select name="surge">' + opts(["none", "light", "moderate", "heavy"], "(skip)") + "</select></label>" +
      '<label>Water temp (°F) <input type="number" name="water_temp_f" min="40" max="90" step="0.5" inputmode="decimal"></label>' +
      '<label>Water color <select name="color">' + opts(OBS_COLORS, "(skip)") + "</select></label>" +
      '<label class="wide">Notes <textarea name="notes" maxlength="280" rows="2" placeholder="Depth, what they bit, how long the stop held"></textarea></label>' +
      '<label>Email <input type="email" name="email" required autocomplete="email" value="' + esc(saved.email || "") + '"><span class="mut small">Only the moderator sees it.</span></label>' +
      '<label>Name (optional) <input type="text" name="name" maxlength="60" autocomplete="name" value="' + esc(saved.name || "") + '"></label>' +
      '</div><div class="send-row"><button type="submit" class="send-btn">Send for review</button><span id="obs-status" class="small" role="status" aria-live="polite"></span></div>' +
      "</form></details>";
  }

  function parseGps(s) {
    var n = (String(s || "").match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
    var lat, lng;
    if (n.length === 2) { lat = n[0]; lng = n[1]; }
    else if (n.length === 4) { lat = Math.abs(n[0]) + n[1] / 60; lng = Math.abs(n[2]) + n[3] / 60; }
    else if (n.length === 6) { lat = Math.abs(n[0]) + n[1] / 60 + n[2] / 3600; lng = Math.abs(n[3]) + n[4] / 60 + n[5] / 3600; }
    else return null;
    if (lng > 0) lng = -lng;
    if (!(lat >= 30.5 && lat <= 34.8 && lng >= -121.5 && lng <= -116.8)) return null;
    return [lat, lng];
  }
  function deviceId() {
    try {
      var id = localStorage.getItem("sd:obs:dev");
      if (!id) {
        var b = new Uint8Array(12); crypto.getRandomValues(b);
        id = Array.prototype.map.call(b, function (x) { return (x < 16 ? "0" : "") + x.toString(16); }).join("");
        localStorage.setItem("sd:obs:dev", id);
      }
      return id;
    } catch (e) { return "anon"; }
  }
  function setPick(lat, lng) {
    var form = document.getElementById("obs-form");
    if (!form) return;
    form.elements.gps.value = lat.toFixed(3) + ", " + lng.toFixed(3);
    if (map) {
      if (pickMarker) pickMarker.remove();
      pickMarker = L.circleMarker([lat, lng], { radius: 8, color: "#ffffff", weight: 2, fill: false }).addTo(map);
    }
  }

  var saidZone = null;
  function setVal(el, v) { if (el && v != null && v !== "") el.value = v; }
  function fillFromSentence(form, draft) {
    var f = form.elements;
    if (draft.type) {
      var r = form.querySelector('input[name=type][value="' + draft.type + '"]');
      if (r) { r.checked = true; r.dispatchEvent(new Event("change")); }
    }
    setVal(f.date, draft.date);
    saidZone = null;
    if (draft.lat != null) {
      if (draft.loc === "zone") {
        var z = zoneOf(draft.lat, draft.lng);
        saidZone = { ll: [draft.lat, draft.lng], label: (z ? zname(z) : "Zone") + " (zone, no GPS)" };
        f.gps.value = saidZone.label;
      } else {
        f.gps.value = draft.lat.toFixed(4) + ", " + draft.lng.toFixed(4);
      }
    }
    if (draft.type === "catch") {
      setVal(f.species, (draft.species || [])[0]); setVal(f.species2, (draft.species || [])[1]);
      var c = draft.counts || {}, first = (draft.species || [])[0];
      setVal(f.count, first ? c[first] : null);
      setVal(f.bait_used, draft.bait); setVal(f.method, draft.method);
    }
    if (draft.type === "sighting") setVal(f.sighting, draft.sighting);
    setVal(f.water_temp_f, draft.water_temp_f);
  }

  function bindForm() {
    var form = document.getElementById("obs-form");
    if (!form) return;
    var status = document.getElementById("obs-status");
    var readBtn = document.getElementById("obs-read");
    if (readBtn) readBtn.addEventListener("click", function () {
      var text = document.getElementById("obs-say").value, st = document.getElementById("obs-say-status");
      st.textContent = "Reading...";
      fetch("/api/reports/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: text }) })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.ok) { st.textContent = j.error || "Couldn't read that."; return; }
          fillFromSentence(form, j.draft);
          st.textContent = j.missing.length ? "Filled what we could. Still need " + j.missing.join(" and ") + "." : "Filled in. Check it and add your email.";
        })
        .catch(function () { st.textContent = "Couldn't reach the reader; fill the form below."; });
    });
    function show() {
      var t = form.querySelector("input[name=type]:checked").value;
      Array.prototype.forEach.call(form.querySelectorAll("[data-for]"), function (el) {
        el.hidden = el.getAttribute("data-for").split(" ").indexOf(t) < 0;
      });
    }
    Array.prototype.forEach.call(form.querySelectorAll("input[name=type]"), function (r) { r.addEventListener("change", show); });
    show();
    document.getElementById("obs-pick").addEventListener("click", function () {
      if (!map) return;
      pickMode = true;
      status.textContent = "Tap the spot on the map.";
      document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
    });
    var here = document.getElementById("obs-here");
    if (!navigator.geolocation) here.hidden = true;
    here.addEventListener("click", function () {
      status.textContent = "Finding you...";
      navigator.geolocation.getCurrentPosition(function (pos) {
        setPick(pos.coords.latitude, pos.coords.longitude);
        status.textContent = "";
      }, function () { status.textContent = "Location isn't available; type the GPS or pick on the map."; }, { timeout: 10000 });
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var f = form.elements, t = form.querySelector("input[name=type]:checked").value;
      var body = { type: t, date: f.date.value, water_temp_f: f.water_temp_f.value, color: f.color.value, notes: f.notes.value,
        email: f.email.value.trim(), name: f.name.value.trim(), deviceId: deviceId() };
      if (t !== "bait") {
        var ll = parseGps(f.gps.value);
        if (!ll && saidZone && f.gps.value === saidZone.label) { ll = saidZone.ll; body.loc = "zone"; }
        if (!ll) { status.textContent = "Add a GPS position in Southern California waters (or pick it on the map)."; return; }
        body.lat = ll[0]; body.lng = ll[1];
      }
      var said = document.getElementById("obs-say");
      if (said && said.value.trim()) body.raw_text = said.value.trim();
      if (t === "catch") {
        body.species = [f.species.value, f.species2.value].filter(Boolean); body.bait = f.bait_used.value;
        body.method = f.method.value; body.size_lb = f.size_lb.value;
        if (f.count.value && body.species[0]) { body.counts = {}; body.counts[body.species[0]] = f.count.value; }
      }
      if (t === "sighting") { body.sighting = f.sighting.value; body.species = f.species_seen.value ? [f.species_seen.value] : []; }
      if (t === "bait") { body.barge = f.barge.value; body.bait = f.bait.value; body.bait_size = f.bait_size.value; }
      if (t === "dive") { body.vis_lo_ft = f.vis_lo_ft.value; body.vis_hi_ft = f.vis_hi_ft.value; body.depth_ft = f.depth_ft.value; body.surge = f.surge.value; }
      try { localStorage.setItem("sd:obs:who", JSON.stringify({ email: body.email, name: body.name })); } catch (e) { /* storage blocked */ }
      var btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      status.textContent = "Sending...";
      fetch("/api/reports/submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function (r) { return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; }); })
        .then(function (j) {
          btn.disabled = false;
          if (!j.ok) { status.textContent = "Not sent: " + (j.error || "try again"); return; }
          status.textContent = "Thanks. It shows here once a moderator approves it.";
          ["gps", "notes", "vis_lo_ft", "vis_hi_ft", "depth_ft", "water_temp_f", "bait_size", "count", "size_lb"].forEach(function (k) { if (f[k]) f[k].value = ""; });
          if (said) said.value = "";
          saidZone = null;
          if (pickMarker) { pickMarker.remove(); pickMarker = null; }
        })
        .catch(function () { btn.disabled = false; status.textContent = "Not sent: the network dropped. Try again."; });
    });
  }
  function openSend() {
    var d = document.getElementById("send");
    if (d) { d.open = true; d.scrollIntoView({ behavior: "smooth", block: "start" }); }
  }

  // --- At a glance: where things are, in one screen ---------------------------
  // Picks out existing numbers (warmest zone, sharpest break...) and adds GPS
  // actions; the full detail stays below.
  function gpsActs(text, lat, lng, tip) {
    return '<span class="acts"><b class="gpsv">' + esc(text) + '</b><button type="button" class="mini-btn" data-copy="' + esc(text) + '">Copy</button>' +
      '<button type="button" class="mini-btn" data-lat="' + esc(lat) + '" data-lng="' + esc(lng) + '" data-tip="' + esc([tip, text].join(" · ")) + '">Map</button></span>';
  }
  function tile(label, valueHtml, linesHtml, actsHtml, href) {
    return '<div class="tile"><div class="k"><span>' + esc(label) + "</span>" + (href ? '<a href="' + esc(href) + '">more</a>' : "") + "</div>" +
      '<div class="v">' + valueHtml + "</div>" + (linesHtml || []).filter(Boolean).map(function (l) { return '<div class="s">' + l + "</div>"; }).join("") +
      (actsHtml || "") + "</div>";
  }
  function zname(z) { return z.short || z.name; }
  function firstFew(names, n) {
    return names.length > n ? names.slice(0, n).join(", ") + " +" + (names.length - n) + " more" : names.join(", ");
  }
  function tilesHtml(tiles) { return '<div class="tiles n' + tiles.length + '">' + tiles.join("") + "</div>"; }
  function byDesc(get) { return function (a, b) { return get(b) - get(a); }; }
  function windiest() {
    var best = null;
    ((facts.weather || {}).outlook || []).forEach(function (row) {
      (row.days || []).forEach(function (d) { if (d.wind_max_kt != null && (!best || d.wind_max_kt > best.d.wind_max_kt)) best = { zone: row.zone, d: d }; });
    });
    return best ? ["Windiest ahead:", best.zone + ",", best.d.wind_dir || "", best.d.wind_max_kt + " kt", best.d.label].join(" ").replace(/\s+/g, " ") : "";
  }
  function weatherTile(filter) {
    var hs = ((facts.weather || {}).headlines || []).filter(filter || function () { return true; });
    return tile("Weather", esc(hs.length ? [hs.length, hs.length > 1 ? "NWS advisories" : "NWS advisory"].join(" ") : "No NWS advisories"),
      hs.slice(0, 2).map(function (h) { return "<b>" + esc(h.event) + "</b> " + esc(["until", h.until, "·", h.where || h.area].join(" ")); })
        .concat([hs.length > 2 ? esc("+" + (hs.length - 2) + " more") : "", esc(windiest())]), "", "#weather");
  }

  function glanceFishing() {
    var all = facts.zones || [], zs = all.filter(function (z) { return z.temp_f != null; }), tiles = [];
    if (zs.length) {
      var warm = zs.slice().sort(byDesc(function (z) { return z.temp_f; }))[0], cool = zs.slice().sort(byDesc(function (z) { return -z.temp_f; }))[0];
      var ch = zs.map(function (z) { return z.temp_change_f; }).filter(function (x) { return x != null; }), days = zs[0].temp_days, chLine = "";
      if (ch.length) {
        var lo = Math.min.apply(null, ch), hi = Math.max.apply(null, ch);
        chLine = hi < 0 ? ["Every zone cooled", Math.abs(hi).toFixed(1) + "-" + Math.abs(lo).toFixed(1) + "°F in", days, "days"].join(" ")
          : lo > 0 ? ["Every zone warmed", lo.toFixed(1) + "-" + hi.toFixed(1) + "°F in", days, "days"].join(" ")
          : ["Zones moved", signed(lo, "°F"), "to", signed(hi, "°F"), "in", days, "days"].join(" ");
      }
      var vn = warm.vs_normal_f != null ? [" ·", signed(warm.vs_normal_f, "°F"), "vs", String(warm.normal_label || "normal").split(",")[0]].join(" ") : "";
      tiles.push(tile("Warmest water", esc(warm.temp_f + "°F"), [esc(zname(warm) + vn), esc(["Coolest:", zname(cool), cool.temp_f + "°F"].join(" ")), esc(chLine)], "", "#zones"));
    }
    var br = all.filter(function (z) { return z["break"]; }).sort(byDesc(function (z) { return z["break"].step_f; }));
    if (br.length) {
      var b0 = br[0]["break"];
      tiles.push(tile("Sharpest break", esc([b0.step_f + "°F over", b0.over_nm, "nm"].join(" ")),
        [esc([zname(br[0]) + ", warm side", b0.warm_side].join(" ")), br[1] ? esc(["Next:", zname(br[1]), br[1]["break"].step_f + "°F"].join(" ")) : ""],
        gpsActs(b0.gps, b0.lat, b0.lng, ["Break", b0.step_f + "°F"].join(" ")), "#zones"));
    }
    var core = all.filter(function (z) { return z.paddy && z.paddy.word === "core"; }), likely = all.filter(function (z) { return z.paddy && z.paddy.word === "likely"; });
    if (core.length || likely.length) {
      var lead = core.length ? core : likely, top = lead[0];
      tiles.push(tile("Paddies, drift model", esc([lead.length, lead.length > 1 ? "zones" : "zone", core.length ? "with a core" : "likely"].join(" ")),
        [esc(firstFew(lead.map(zname), 3)), core.length && likely.length ? esc("Likely: " + firstFew(likely.map(zname), 3)) : ""],
        gpsActs(top.paddy.gps, top.paddy.lat, top.paddy.lng, "Paddies (" + top.paddy.word + ")"), "#zones"));
    }
    var fz = all.filter(function (z) { return (z.fleet || {}).last3_hours > 0; }).sort(byDesc(function (z) { return z.fleet.last3_hours; }));
    var ly = all.filter(function (z) { return (z.fleet || {}).last_year_hours > 0; }).sort(byDesc(function (z) { return z.fleet.last_year_hours; }));
    var atSea = (((facts.fishing || {}).fleet_now || {}).at_sea || []).length;
    var best = bestSpots();
    if (best.length && (best[0].total || Object.keys(best[0].fish).length)) {
      var b0 = best[0], t0 = b0.top;
      tiles.unshift(tile("Where the fish are", esc(b0.place),
        [esc(catchLine(b0.fish, 3) || Object.keys(b0.fish).join(", ")), esc(Object.keys(b0.boats).slice(0, 2).join(", ")),
         best[1] ? esc(["Next:", best[1].place, catchLine(best[1].fish, 2)].join(" ").trim()) : ""],
        t0 && !t0.zoneOnly ? gpsActs(ddm(t0.lat, t0.lng), t0.lat, t0.lng, b0.place) : "", "#hot"));
    }
    tiles.push(tile("Where the fleet stopped", esc(fz.length ? fz[0].fleet.last3_hours + " h" : "No offshore stops"),
      [fz.length ? esc([zname(fz[0]) + ", last 3 days"].concat(fz[1] ? ["· then", zname(fz[1]), fz[1].fleet.last3_hours + " h"] : []).join(" ")) : "",
       ly.length ? esc(["Same weeks last year:", zname(ly[0]), ly[0].fleet.last_year_hours + " h"].join(" ")) : "",
       atSea ? '<a href="#fleet">' + esc([atSea, atSea > 1 ? "boats out now" : "boat out now"].join(" ")) + "</a>" : ""], "", "#zones"));
    var rec = obsRecent(["catch", "sighting"]);
    tiles.push(tile("From the water", esc(rec.length ? [rec.length, rec.length > 1 ? "reports" : "report", "this week"].join(" ") : "No reports yet"),
      rec.length ? rec.slice(0, 2).map(function (o) { var z = zoneOf(o.lat, o.lng); return esc([obsWhat(o), z ? "at " + zname(z) : "", "(" + md(o.date) + ")"].join(" ")); })
        : [sendLink("Send what you're seeing")], "", "#water"));
    tiles.push(weatherTile());
    return '<section class="glance"><h2 id="glance">At a glance</h2>' + tilesHtml(tiles) + "</section>";
  }

  function visChip(ft) {
    var c = visColor(ft), lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
    return '<span class="vchip" style="background:rgb(' + c.map(Math.round).join(",") + ");color:" + (lum > 150 ? "#0b1220" : "#ffffff") + '">' + esc(ft + " ft") + "</span>";
  }
  function glanceDiving() {
    var sites = facts.dive_sites || [];
    var head = ["Site", "Visibility, predicted", "Last seen", "Sets today", "Water", "Surge"];
    var rows = sites.map(function (site) {
      var vp = site.vis_pred, posts = site.vis || [], lp = posts.length ? posts[posts.length - 1] : null;
      var dv = obsRecent(["dive"]).filter(function (o) { return nearestSite(o) === site; });
      var seen = dv.length ? esc(visRange(dv[0]) + " ft") + ' <span class="mut small">' + esc(["divers,", md(dv[0].date)].join(" ")) + "</span>"
        : lp ? esc([lp.lo_ft === lp.hi_ft ? lp.lo_ft : [lp.lo_ft, lp.hi_ft].join("-"), "ft"].join(" ")) + ' <span class="mut small">' + esc(["shop,", md(lp.date)].join(" ")) + "</span>"
        : '<span class="mut">no posts</span>';
      var today = (site.surf || [])[0], temps = (site.temp_series || []).map(function (x) { return x.temp_f; });
      var cells = [
        esc(site.short),
        vp ? visChip(vp.ft) + (vp.lo_ft != null ? ' <span class="mut small">' + esc([vp.lo_ft, vp.hi_ft].join("-")) + "</span>" : "") : '<span class="mut">n/a</span>',
        seen,
        today ? esc([today.sets_ft < 1 ? "under 1" : today.sets_ft.toFixed(0), "ft at", today.tp_s, "s"].join(" ")) : "",
        temps.length ? esc(temps[temps.length - 1] + "°F") : "",
        site.surge ? esc(site.surge.level) : "",
      ];
      return "<tr>" + cells.map(function (c, i) { return "<td" + (i ? ' data-l="' + esc(head[i]) + '"' : "") + ">" + c + "</td>"; }).join("") + "</tr>";
    }).join("");
    var table = sites.length ? '<div class="scroll"><table class="stable"><thead><tr>' + head.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + rows + '</tbody></table></div><p class="note">Visibility in the map\'s colors: our model scaled to recent shop posts (see Trends for how it has done). Sets are the CDIP nearshore model at the entry; surge is bottom water speed.</p>' : "";
    var tiles = [];
    tiles.push(weatherTile(function (h) { return (h.zones || []).some(function (zid) { return /^CAZ|PZZ740|PZZ655/.test(zid); }); }));
    var w = facts.water || {}, tide = ((w.tide || {}).events || []);
    if (tide.length || w.sun) {
      tiles.push(tile("Tide and light", esc(tide.length ? [tide[0].type, tide[0].ft + " ft", tide[0].time].join(" ") : ""),
        tide.slice(1).map(function (e) { return esc([e.type, e.ft + " ft", e.time].join(" ")); }).concat([w.sun ? esc(["Sun up", w.sun.rise + ", down", w.sun.set].join(" ")) : ""]), "", "#basics"));
    }
    var hunt = ((facts.fishing || {}).inshore || []).filter(function (r) { return r.spear; });
    if (hunt.length) {
      tiles.push(tile("What's around", esc(hunt.slice(0, 2).map(function (r) { return r.label.split(" (")[0]; }).join(", ")),
        hunt.slice(0, 5).map(function (r) { return esc([r.label.split(" (")[0] + ":", r.status].join(" ")); }), "", "#around"));
    }
    var rec = obsRecent(["dive", "catch", "sighting"]);
    tiles.push(tile("From the water", esc(rec.length ? [rec.length, rec.length > 1 ? "reports" : "report", "this week"].join(" ") : "No reports yet"),
      rec.length ? rec.slice(0, 2).map(function (o) { return esc([obsWhat(o), "(" + md(o.date) + ")"].join(" ")); }) : [sendLink("Send your dive")], "", "#water"));
    return '<section class="glance"><h2 id="glance">At a glance</h2>' + table + tilesHtml(tiles) + "</section>";
  }

  // Sticky links to each section, instead of a long scroll.
  function sectionNav() {
    var items = view === "fishing"
      ? [["glance", "At a glance"], ["hot", "Hot Bites"], ["species", "Species"], ["zones", "Zones"], ["weather", "Weather"], ["water", "Reports"], ["trends", "Trends"], ["season", "Season"], ["bait", "Bait"]]
      : [["glance", "At a glance"], ["map-h", "Map"], ["hot", "Hot Bites"], ["species", "Species"], ["water", "Reports"], ["weather", "Weather"], ["trends", "Trends"], ["season", "Season"], ["sites", "Site details"]];
    return '<nav class="jump" aria-label="Sections">' + items.map(function (i) { return '<a href="#' + i[0] + '">' + esc(i[1]) + "</a>"; }).join("") + "</nav>";
  }
  function chartsBlock(prim, more) {
    return '<div class="charts">' + prim.join("") + "</div>" +
      (more.length ? '<details class="more"><summary>' + esc(["More charts", "(" + more.length + ")"].join(" ")) + '</summary><div class="charts">' + more.join("") + "</div></details>" : "");
  }

  // --- Hot Bites: where fish were reported and where the fleet sat on a spot -----
  // Reports are approved field reports (species, place, how); fleet points are
  // AIS clusters (pipeline/reports/hot_bites.py): boats and minutes, never species.
  var HB = { days: 4, species: "" }, hotLayer = null;
  var HB_DAYS = [[1, "Today"], [2, "2 days"], [3, "3 days"], [4, "4 days"]];
  function hotPoints() {
    var pts = [];
    obsRecent(["catch", "sighting"]).forEach(function (o) {
      var a = obsAge(o);
      if (a > 3) return;
      pts.push({ kind: "report", date: o.date, age: a, lat: o.lat, lng: o.lng, zoneOnly: o.loc === "zone", species: o.species || [], catchMap: o.counts || {},
        what: obsWhat(o), how: o.type === "sighting" ? "sighting" : "",
        extra: [o.size_lb ? "biggest " + o.size_lb + " lb" : "", o.water_temp_f != null ? o.water_temp_f + "°F" : "", o.via && o.via !== "web" ? "by " + o.via : ""].filter(Boolean).join(", "),
        confirmed: o.confidence === "confirmed" });
    });
    ((facts.hot_bites || {}).fleet || []).forEach(function (h) {
      var a = Math.round(dayNum(facts.date) - dayNum(h.date));
      if (a < 0 || a > 3) return;
      var boats = h.boats || [], catchKeys = Object.keys(h.catch || {});
      var lead = boats[0] || {};
      pts.push({ kind: catchKeys.length ? "dock" : "fleet", date: h.date, age: a, lat: h.lat, lng: h.lng, species: catchKeys, zone: h.zone,
        place: h.place, nBoats: h.n_boats || boats.length, catchMap: h.catch || {}, boatList: boats,
        what: catchKeys.length ? catchLine(h.catch, 4)
          : (h.n_boats > 1 ? [h.n_boats, "boats on one spot"].join(" ") : [lead.name || "A boat", "held a spot"].join(" ")),
        how: boats.map(boatLine).join("; ") + " · " + [h.minutes, "min stopped"].join(" ") });
    });
    return pts.sort(function (a, b) { return a.age - b.age || (a.kind === "report" ? -1 : 1) - (b.kind === "report" ? -1 : 1); });
  }
  function hotFiltered() {
    return hotPoints().filter(function (p) {
      if (p.age >= HB.days) return false;
      if (!HB.species) return true;
      if (HB.species === "_fleet") return p.kind === "fleet";
      return p.species.indexOf(HB.species) >= 0;
    });
  }
  // A place readers know: the named spot within 4 nm, else the zone.
  function spotName(lat, lng) {
    var best = null;
    ((facts.fishing || {}).spots || []).forEach(function (sp) {
      var d = nm([lat, lng], [sp.lat, sp.lng]);
      if (d <= 4 && (!best || d < best.d)) best = { d: d, name: /^\d+$/.test(sp.name) ? "the " + sp.name : sp.name };
    });
    return best ? best.name : null;
  }
  function hotPlace(p) {
    if (p.place) return p.place;
    var sp = spotName(p.lat, p.lng);
    if (sp) return sp;
    var z = p.zone ? null : zoneOf(p.lat, p.lng);
    return p.zone || (z ? zname(z) : "outside the zones");
  }
  // Game fish lead, then by count: "186 yellowfin, 86 yellowtail" before "215 bullet tuna".
  var GAME = ["bluefin", "yellowfin", "yellowtail", "dorado", "wahoo", "striped marlin", "white seabass", "halibut", "calico bass", "lingcod"];
  function catchLine(c, n) {
    var rank = function (k) { var i = GAME.indexOf(k); return i < 0 ? 1 : 0; };
    return Object.keys(c || {}).filter(function (k) { return c[k] > 0; }).sort(function (a, b) { return rank(a) - rank(b) || c[b] - c[a]; })
      .slice(0, n || 4).map(function (k) { return [c[k].toLocaleString("en-US"), k].join(" "); }).join(", ");
  }
  function boatLine(b) {
    return b.name + (b.source || b.landing ? " (" + [b.source || b.landing, b.trip].filter(Boolean).join(", ") + ")" : "");
  }
  function hotList() {
    var pts = hotFiltered();
    if (!pts.length) return '<p class="small mut">Nothing in this window yet. ' + sendLink("Send a report") + " when you find fish.</p>";
    return '<ul class="plain hb">' + pts.map(function (p) {
      var g = ddm(p.lat, p.lng);
      return '<li class="hb-' + esc(p.kind) + '"><span class="age a' + esc(p.age) + '">' + esc(p.age === 0 ? "today" : p.age + "d") + "</span><b>" + esc(p.what) + "</b>" +
        tagHtml(p.kind === "fleet" ? "AIS fleet" : p.kind === "dock" ? "dock count + AIS" : p.confirmed ? "confirmed" : "report") +
        '<div class="small">' + esc([hotPlace(p), p.how, p.extra].filter(Boolean).join(" · ")) + "</div>" +
        '<div class="mut small">' + esc(md(p.date)) + " · " + (p.zoneOnly ? esc("zone named, no GPS") : '<span class="gps">' + esc(g) + '</span><button type="button" class="mini-btn" data-copy="' + esc(g) +
          '">Copy</button><button type="button" class="mini-btn" data-lat="' + esc(p.lat) + '" data-lng="' + esc(p.lng) + '" data-tip="' + esc(p.what + " · " + g) + '">Map</button>') + "</div></li>";
    }).join("") + "</ul>";
  }
  function bestSpots() {
    var groups = {};
    hotFiltered().forEach(function (p) {
      var key = hotPlace(p), g = groups[key] || (groups[key] = { place: key, fish: {}, total: 0, reports: 0, boats: {}, fleet: 0, latest: p.date, top: p, topFish: -1 });
      var c = p.catchMap || {};
      var fishHere = 0;
      Object.keys(c).forEach(function (k) { g.fish[k] = (g.fish[k] || 0) + c[k]; fishHere += c[k]; });
      p.species.forEach(function (k) { if (!(k in g.fish)) g.fish[k] = g.fish[k] || 0; });
      g.total += fishHere;
      if (p.kind === "report") g.reports += 1; else g.fleet += 1;
      (p.boatList || []).forEach(function (b) { g.boats[b.name] = b; });
      if (p.date > g.latest) g.latest = p.date;
      if (fishHere > g.topFish && !p.zoneOnly) { g.top = p; g.topFish = fishHere; }
    });
    return Object.keys(groups).map(function (k) { return groups[k]; })
      .filter(function (g) { return Object.keys(g.fish).length || g.fleet; })
      .sort(function (a, b) { return b.total - a.total || (b.reports + b.fleet) - (a.reports + a.fleet) || Object.keys(b.boats).length - Object.keys(a.boats).length; });
  }
  function bestSpotsHtml() {
    var rows = bestSpots().slice(0, 5);
    if (!rows.length) return '<p class="small mut">No fish reported or counted in this window yet.</p>';
    return '<ol class="best">' + rows.map(function (g) {
      var fishTxt = catchLine(Object.keys(g.fish).reduce(function (o, k) { if (g.fish[k]) o[k] = g.fish[k]; return o; }, {}), 5);
      var named = Object.keys(g.fish).filter(function (k) { return !g.fish[k]; });
      var t = g.top, gp = t && !t.zoneOnly ? ddm(t.lat, t.lng) : null;
      var boats = Object.keys(g.boats).map(function (n) { return boatLine(g.boats[n]); });
      return "<li><b>" + esc(g.place) + "</b>" + (fishTxt ? " " + esc(fishTxt) : "") + (named.length ? '<span class="mut">' + esc((fishTxt ? "; also " : " ") + named.join(", ")) + "</span>" : "") +
        '<div class="small">' + esc([boats.length ? boats.slice(0, 3).join("; ") + (boats.length > 3 ? " +" + (boats.length - 3) : "") : "",
          g.reports ? [g.reports, g.reports > 1 ? "reports" : "report"].join(" ") : "", "latest " + md(g.latest)].filter(Boolean).join(" · ")) + "</div>" +
        (gp ? '<div class="mut small"><span class="gps">' + esc(gp) + '</span><button type="button" class="mini-btn" data-copy="' + esc(gp) + '">Copy</button><button type="button" class="mini-btn" data-lat="' +
          esc(t.lat) + '" data-lng="' + esc(t.lng) + '" data-tip="' + esc(g.place + " · " + gp) + '">Map</button></div>' : "") + "</li>";
    }).join("") + "</ol>";
  }
  function landedHtml() {
    var rows = ((facts.hot_bites || {}).landed || []).filter(function (t) {
      var a = Math.round(dayNum(facts.date) - dayNum(t.date));
      if (a < 0 || a >= HB.days) return false;
      return !HB.species || HB.species === "_fleet" ? !HB.species : (t.catch || {})[HB.species] > 0;
    });
    if (!rows.length) return "";
    return '<details class="more"><summary>' + esc(["Landed with no AIS position", "(" + rows.length + ")"].join(" ")) + '</summary><div class="scroll"><table class="mini"><tbody>' +
      rows.map(function (t) {
        return "<tr><td>" + esc(md(t.date)) + "</td><td><b>" + esc(t.boat) + "</b></td><td>" + esc([t.landing, t.trip].filter(Boolean).join(", ")) + "</td><td>" + esc(catchLine(t.catch, 5)) + "</td></tr>";
      }).join("") + '</tbody></table></div><p class="note">These trips landed fish but the shore receivers did not hear where the boat stopped (most boats under 65 ft carry no AIS, and range ends 30-50 nm out).</p></details>';
  }

  function drawHot() {
    if (!hotLayer) return;
    hotLayer.clearLayers();
    hotFiltered().forEach(function (p) {
      var op = [1, 0.8, 0.6, 0.45][p.age] || 0.45;
      var tip = esc(p.what) + ", " + esc(md(p.date));
      if (p.kind === "fleet" || p.kind === "dock") {
        L.circleMarker([p.lat, p.lng], { radius: 5 + Math.min(p.nBoats || 1, 6) * 1.5, color: p.kind === "dock" ? "#ffffff" : "#0b1220", weight: p.kind === "dock" ? 2.5 : 1.5,
          fillColor: "#f97316", fillOpacity: op, opacity: op })
          .bindTooltip(tip + " · " + esc(hotPlace(p)), { direction: "top" }).addTo(hotLayer);
      } else if (p.zoneOnly) {
        L.circleMarker([p.lat, p.lng], { radius: 14, color: "#e879f9", weight: 2, dashArray: "4 4", fill: false, opacity: op })
          .bindTooltip(tip + " (zone, no GPS)", { direction: "top" }).addTo(hotLayer);
      } else {
        L.marker([p.lat, p.lng], { icon: L.divIcon({ className: "obs-pin hot", html: '<i style="opacity:' + op + '"></i>', iconSize: [16, 16] }), keyboard: false })
          .bindTooltip(tip, { direction: "top" }).addTo(hotLayer);
      }
    });
  }
  function hotGpx() {
    var x = function (v) { return String(v).replace(/[<>&"]/g, function (c) { return { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]; }); };
    return '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="ShouldIDive Ocean Report" xmlns="http://www.topografix.com/GPX/1/1">\n' +
      hotFiltered().filter(function (p) { return !p.zoneOnly; }).map(function (p) {
        return '<wpt lat="' + p.lat.toFixed(5) + '" lon="' + p.lng.toFixed(5) + '"><time>' + x(p.date) + "T12:00:00Z</time><name>" + x((hotPlace(p) + ": " + p.what).slice(0, 40)) +
          "</name><desc>" + x([p.date, hotPlace(p), p.how].filter(Boolean).join(" · ")) + "</desc></wpt>\n";
      }).join("") + "</gpx>\n";
  }
  function hotSection(withMap) {
    var counts = {};
    hotPoints().forEach(function (p) { p.species.forEach(function (sp) { counts[sp] = (counts[sp] || 0) + 1; }); });
    ((facts.hot_bites || {}).landed || []).forEach(function (t) { Object.keys(t.catch || {}).forEach(function (sp) { counts[sp] = (counts[sp] || 0) + 1; }); });
    var chips = HB_DAYS.map(function (d) { return '<button type="button" class="chip' + (HB.days === d[0] ? " on" : "") + '" data-hbdays="' + d[0] + '">' + esc(d[1]) + "</button>"; }).join("");
    var sel = '<select id="hb-species" aria-label="Species"><option value="">All fish and fleet</option>' + Object.keys(counts).sort().map(function (sp) {
      return '<option value="' + esc(sp) + '"' + (HB.species === sp ? " selected" : "") + ">" + esc([sp, "(" + counts[sp] + ")"].join(" ")) + "</option>";
    }).join("") + '<option value="_fleet"' + (HB.species === "_fleet" ? " selected" : "") + ">Fleet only (no species)</option></select>";
    var dockSrc = (facts.hot_bites || {}).dock_sources || [];
    return '<section><h2 id="hot">Hot Bites</h2><div class="hb-ctl">' + chips + sel + '<button type="button" class="mini-btn" id="hb-gpx">Download GPX</button></div>' +
      '<h3>Where the fish are</h3><div id="hb-best">' + bestSpotsHtml() + "</div>" +
      (withMap ? mapBlock() : "") + '<h3>Every report and boat</h3><div id="hb-list">' + hotList() + "</div>" + '<div id="hb-landed">' + landedHtml() + "</div>" +
      (dockSrc.length ? '<p class="note">Dock counts from ' + dockSrc.map(function (d) { return link(d.url, d.landing); }).join(" and ") +
        ", joined to where AIS heard each boat sit longest that day; the count is the whole trip's.</p>" : "") +
      '<p class="note">Reports come from people on the water and show once a moderator approves them; a report that names a spot but no GPS sits at the zone\'s center. ' +
      "Fleet points are AIS: boats that stopped and held a spot offshore, which says where they fished, not what they caught. GPX opens in most chartplotters and phone apps.</p></section>";
  }
  function bindHot() {
    var list = document.getElementById("hb-list");
    if (!list) return;
    function refresh() {
      list.innerHTML = hotList();
      bindGpsIn(list);
      var best = document.getElementById("hb-best"), landed = document.getElementById("hb-landed");
      if (best) { best.innerHTML = bestSpotsHtml(); bindGpsIn(best); }
      if (landed) landed.innerHTML = landedHtml();
      drawHot();
      Array.prototype.forEach.call(main.querySelectorAll(".hb-ctl .chip"), function (c) { c.classList.toggle("on", +c.getAttribute("data-hbdays") === HB.days); });
    }
    Array.prototype.forEach.call(main.querySelectorAll(".hb-ctl .chip"), function (c) {
      c.addEventListener("click", function () { HB.days = +c.getAttribute("data-hbdays"); refresh(); });
    });
    var sel = document.getElementById("hb-species");
    if (sel) sel.addEventListener("change", function () { HB.species = sel.value; refresh(); });
    var gpxBtn = document.getElementById("hb-gpx");
    if (gpxBtn) gpxBtn.addEventListener("click", function () {
      var blob = new Blob([hotGpx()], { type: "application/gpx+xml" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "shouldidive-hot-bites-" + facts.date + ".gpx";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    });
  }

  // --- Species board: each fish, where it was reported, how, and where the water fits ---
  var FIT_NAME = { yellowfin: "Yellowfin tuna", bluefin: "Bluefin tuna", dorado: "Dorado", "striped marlin": "Striped marlin", wahoo: "Wahoo", yellowtail: "Yellowtail" };
  var INSHORE_KEY = { yellowtail: "yellowtail", "white seabass": "wsb", "calico bass": "calico", halibut: "halibut", sheephead: "sheephead" };
  var SPEAR_SPECIES = ["yellowtail", "white seabass", "calico bass", "halibut", "sheephead", "lobster", "rockfish", "lingcod"];
  function tally(list) {
    return Object.keys(list).sort(function (a, b) { return list[b] - list[a]; }).slice(0, 3).map(function (k) { return list[k] > 1 ? k + " (" + list[k] + ")" : k; }).join(", ");
  }
  function speciesBoard() {
    var cur = obsRecent(["catch", "sighting"]);
    var prev = OBS.filter(function (o) { var a = obsAge(o); return (o.type === "catch" || o.type === "sighting") && a >= OBS_DAYS && a < 2 * OBS_DAYS; });
    var inshore = (facts.fishing || {}).inshore || [];
    var landedWeek = (facts.hot_bites || {}).dock_week || {};
    var pool = view === "fishing" ? OBS_SPECIES.filter(function (sp) { return sp !== "other"; }) : SPEAR_SPECIES;
    if (view === "fishing") Object.keys(landedWeek).forEach(function (sp) { if (pool.indexOf(sp) < 0 && sp !== "bass") pool = pool.concat([sp]); });
    var rows = pool.map(function (sp) {
      var has = function (o) { return (o.species || []).indexOf(sp) >= 0; };
      var mine = cur.filter(has), before = prev.filter(has).length, zc = {}, how = {}, fish = 0, big = 0, temps = [];
      mine.forEach(function (o) {
        var z = zoneOf(o.lat, o.lng), k = z ? zname(z) : "outside the zones";
        zc[k] = (zc[k] || 0) + 1;
        [o.method, o.bait && o.bait !== "none" ? o.bait : ""].filter(Boolean).forEach(function (h) { how[h] = (how[h] || 0) + 1; });
        fish += (o.counts || {})[sp] || 0;
        big = Math.max(big, o.size_lb || 0);
        if (o.water_temp_f != null) temps.push(o.water_temp_f);
      });
      var fits = FIT_NAME[sp] ? (facts.zones || []).filter(function (z) { return (z.water_fits || []).indexOf(FIT_NAME[sp]) >= 0; }).map(zname) : [];
      var ins = inshore.filter(function (r) { return r.key === INSHORE_KEY[sp]; })[0];
      var lw = landedWeek[sp] || {}, landed = Object.keys(lw).reduce(function (t, k) { return t + lw[k]; }, 0);
      return { sp: sp, n: mine.length, before: before, zc: zc, how: how, fish: fish, big: big, temps: temps, fits: fits, season: ins ? ins.status : "", landed: landed, lw: lw };
    }).filter(function (r) { return r.n || r.before || r.landed || r.fits.length || r.season; })
      .sort(function (a, b) { return b.landed - a.landed || b.n - a.n || b.fits.length - a.fits.length; });
    if (!rows.length) return "";
    var head = ["Species", "Landed, 7 days", "Reports, 7 days", "Where reported", "How", "Biggest, water", "Water fits or season"];
    var body = rows.map(function (r) {
      var trend = r.n || r.before ? [r.n, r.before ? (r.n > r.before ? "(up from " + r.before + ")" : r.n < r.before ? "(down from " + r.before + ")" : "(same)") : r.n ? "(new)" : ""].join(" ") : "none yet";
      var cells = [
        "<b>" + esc(r.sp) + "</b>",
        r.landed ? esc(r.landed.toLocaleString("en-US")) + '<div class="mut small">' + esc(Object.keys(r.lw).map(function (k) { return [k, r.lw[k].toLocaleString("en-US")].join(" "); }).join(", ")) + "</div>" : "",
        esc(trend) + (r.fish ? '<div class="mut small">' + esc(r.fish + " fish counted") + "</div>" : ""),
        r.n ? esc(tally(r.zc)) : "",
        r.n ? esc(tally(r.how)) || '<span class="mut">not said</span>' : "",
        esc([r.big ? r.big + " lb" : "", r.temps.length ? Math.min.apply(null, r.temps) + (Math.max.apply(null, r.temps) !== Math.min.apply(null, r.temps) ? "-" + Math.max.apply(null, r.temps) : "") + "°F" : ""].filter(Boolean).join(", ")),
        r.fits.length ? esc(firstFew(r.fits, 3)) : r.season ? esc("Season: " + r.season) : "",
      ];
      return "<tr>" + cells.map(function (c, i) { return "<td" + (i ? ' data-l="' + esc(head[i]) + '"' : "") + ">" + c + "</td>"; }).join("") + "</tr>";
    }).join("");
    return '<section><h2 id="species">' + esc(view === "fishing" ? "Species" : "Species for divers") + '</h2><div class="scroll"><table class="stable sboard"><thead><tr>' +
      head.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      '<p class="note">"Landed" is the dock counts the landings publish themselves (Dana Wharf, Davey\'s Locker); the core San Diego and LA landings publish through a network whose terms forbid reuse, so they are not counted here. Reports and "how" come from approved reports in the last ' + OBS_DAYS + ' days, against the week before. "Water fits" lists the zones where this month\'s satellite water matches the species\' usual range; it is not a catch.</p></section>';
  }

  // --- Sections -------------------------------------------------------------------
  function mapSection() {
    return '<section><h2 id="map-h">Where things are</h2>' + mapBlock() + "</section>";
  }
  function mapBlock() {
    return '<div id="map" class="map" role="region" aria-label="Map of hot bites, water temperature, zones, breaks, paddies and fleet"></div>' +
      (view === "fishing" ? '<div class="maplegend"><span class="mut small">' + SST_SHOW_F[0] + '°F</span><span class="ramp" aria-hidden="true"></span><span class="mut small">' + SST_SHOW_F[1] + "°F</span></div>"
        : '<div class="maplegend"><span class="mut small">Predicted visibility</span><span class="ramp vis" aria-hidden="true">' +
          VIS_RAMP.map(function (s) { return '<b style="left:' + (s[0] / VIS_SHOW_FT[1] * 100) + '%">' + s[0] + (s[0] === VIS_SHOW_FT[1] ? "+" : "") + "</b>"; }).join("") +
          '</span><span class="mut small">ft</span></div>') +
      '<div class="maplegend"><span class="mut small">Fleet stops:</span>' + ["today", "1 day", "2 days", "3 days"].map(function (t, i) {
        return '<span class="key"><i style="background:' + AGE_COLORS[i] + '"></i>' + t + "</span>";
      }).join("") + '<span class="key"><i style="background:#fbbf24"></i>boat now</span><span class="key"><i class="ring"></i>break</span>' + (view === "fishing" ? '<span class="key"><i style="background:#22c55e"></i>paddies</span>' : "") +
      '<span class="key"><i class="dia"></i>reported fish</span><span class="key"><i class="hbf"></i>fleet on a spot</span></div>' +
      '<p class="note">' + (view === "fishing" ? "Water temperature: " + esc(sstAsOf()) + " satellite map; blank is cloud or no data."
        : "Predicted visibility: our model scaled to where recent shop reports landed, La Jolla and Laguna each on their own record; in between and beyond, the two are blended by distance, an extrapolation. Water temperature is in the layer menu.") + "</p>";
  }

  // Predicted visibility overlay in the main map's colors (VIZ_RAMP in
  // src/components/DataOverlay.jsx): burnt orange for murky, green at 10 ft,
  // then deepening blues to navy at 50 ft, after the model's recent bias.
  var VIS_RAMP = [[0, [194, 65, 12]], [10, [34, 197, 94]], [20, [6, 182, 212]], [30, [3, 105, 161]], [50, [31, 77, 117]]];
  var VIS_SHOW_FT = [0, 50];
  function visColor(ft) {
    if (ft <= VIS_RAMP[0][0]) return VIS_RAMP[0][1];
    for (var i = 0; i < VIS_RAMP.length - 1; i++) {
      var a = VIS_RAMP[i], b = VIS_RAMP[i + 1];
      if (ft <= b[0]) {
        var k = (ft - a[0]) / (b[0] - a[0]);
        return [a[1][0] + (b[1][0] - a[1][0]) * k, a[1][1] + (b[1][1] - a[1][1]) * k, a[1][2] + (b[1][2] - a[1][2]) * k];
      }
    }
    return VIS_RAMP[VIS_RAMP.length - 1][1];
  }
  // Each pixel blends the areas' scales (La Jolla, Laguna) by inverse squared
  // distance to their reference points, the same rule the pipeline uses
  // (visibility.cal_at), so the correction has no seam between them.
  function visScaleAt(V, lat, lng) {
    var areas = V.areas || {}, wsum = 0, ssum = 0;
    Object.keys(areas).forEach(function (k) {
      var a = areas[k];
      if (!a.ref || !a.applied) return;
      var dy = lat - a.ref[0], dx = (lng - a.ref[1]) * Math.cos(lat * Math.PI / 180), dd = Math.max(dx * dx + dy * dy, 1e-6);
      wsum += 1 / dd; ssum += a.scale / dd;
    });
    if (wsum) return ssum / wsum;
    return (V.calibration && V.calibration.applied) ? V.calibration.scale : 1;
  }
  function drawVis(V, bb, layer) {
    var rng = V.range_ft || [0, 80];
    var img = new Image();
    img.onload = function () {
      var cv = document.createElement("canvas");
      cv.width = img.width; cv.height = img.height;
      var ctx = cv.getContext("2d");
      ctx.drawImage(img, 0, 0);
      var px = ctx.getImageData(0, 0, cv.width, cv.height), d = px.data, w = cv.width, h = cv.height;
      for (var i = 0; i < d.length; i += 4) {
        var v = d[i];
        if (!v) { d[i + 3] = 0; continue; }
        var pi = i / 4, x = pi % w, y = Math.floor(pi / w);
        var scale = visScaleAt(V, bb[3] - (y + 0.5) / h * (bb[3] - bb[1]), bb[0] + (x + 0.5) / w * (bb[2] - bb[0]));
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
    // Diving opens on visibility; paddies start off there because their green
    // would sit on the visibility ramp's green (still in the layer menu).
    if (view !== "fishing") { map.removeLayer(sstLayer); map.removeLayer(padLayer); visLayer.addTo(map); }
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
    hotLayer = L.layerGroup().addTo(map);
    drawHot();
    var obsLayer = L.layerGroup().addTo(map);
    obsRecent().forEach(function (o) {
      var a = obsAge(o), op = OBS_AGE_OPACITY[a === 0 ? 0 : a <= 2 ? 1 : 2];
      L.marker([o.lat, o.lng], { icon: L.divIcon({ className: "obs-pin", html: '<i style="opacity:' + op + '"></i>', iconSize: [12, 12] }), keyboard: false })
        .bindTooltip(esc(obsWhat(o)) + ", " + esc(md(o.date)) + (o.confidence === "confirmed" ? " · confirmed" : ""), { direction: "top" }).addTo(obsLayer);
    });
    map.on("click", function (ev) {
      if (!pickMode) return;
      pickMode = false;
      setPick(ev.latlng.lat, ev.latlng.lng);
      var st = document.getElementById("obs-status");
      if (st) st.textContent = "Position set from the map.";
      openSend();
    });
    L.control.layers(null, { "Water temperature": sstLayer, "Predicted visibility": visLayer, "Zones and breaks": zoneLayer, "Paddies": padLayer,
      "Fleet stops, 3 days": fleetLayer, "Boats now": liveLayer, "Hot bites": hotLayer, "Field reports, 7 days": obsLayer, "Named spots": spotLayer }, { position: "topright", collapsed: true }).addTo(map);
    // Hot bites carry the last 4 days of reports and fleet clusters; the raw layers stay in the menu.
    if (view === "fishing") { map.removeLayer(obsLayer); map.removeLayer(fleetLayer); }
  }

  function weather() {
    var w = facts.weather || {};
    var times = [srcTime("cwf_sgx"), srcTime("cwf_lox")];
    var syn = (w.synopsis || []).map(function (s, i) {
      if (!s.text) return "";
      return '<div class="syn"><div class="mut small">' + esc(s.office) + (times[i] ? ", issued " + esc(times[i]) : "") + "</div><blockquote>" + esc(s.text) + "</blockquote></div>";
    }).join("");
    var heads = (w.headlines || []).length ? '<ul class="plain heads">' + w.headlines.map(function (h) {
      return "<li><b>" + esc(h.event) + "</b>" + (h.until ? " until " + esc(h.until) : "") + ' <span class="mut small">· ' + esc(h.where || h.area) + "</span>" +
        (h.description ? '<details class="inline"><summary>NWS text</summary><p>' + esc(h.description) + "</p></details>" : "") + "</li>";
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
    return '<section><h2 id="weather">Weather</h2>' + heads + "<h3>Next three days</h3>" + grid +
      (now ? '<p class="small">Wind at the buoys now: ' + now + ".</p>" : "") +
      (syn ? '<details class="more"><summary>NWS forecast discussion</summary>' + syn + "</details>" : "") + "</section>";
  }

  function zoneCard(z) {
    var f = z.fleet || {}, b = z["break"], p = z.paddy;
    var temps = (z.temp_series || []).map(function (s) { return s.temp_f; });
    var series = f.series || [];
    var gps = [];
    if (b) gps.push({ label: "Break", text: b.gps, lat: b.lat, lng: b.lng, tip: ["Break", b.step_f + "°F over", b.over_nm, "nm"].join(" ") });
    if (p) gps.push({ label: ["Paddies", "(" + String(p.word) + ")"].join(" "), text: p.gps, lat: p.lat, lng: p.lng, tip: ["Paddy region", "(" + String(p.word) + ")"].join(" ") });
    return '<article class="zone"><header><h3>' + esc(z.name) + '</h3><span class="mut small">' + esc(z.nm_from_sd) + " nm</span></header>" +
      '<div class="mut small spots">' + esc(z.spots) + "</div>" +
      (z.notice ? '<div class="notice small">' + (z.notice.url ? link(z.notice.url, z.notice.text) : esc(z.notice.text)) + "</div>" : "") +
      '<div class="row"><div class="metric"><span class="k">Water</span><span class="v">' + (z.temp_f != null ? esc(z.temp_f) + "°F" : "n/a") + "</span>" +
      (z.temp_change_f != null ? '<span class="d">' + esc(signed(z.temp_change_f, "°F")) + " in " + esc(z.temp_days) + " days</span>" : "") +
      (z.vs_normal_f != null ? '<span class="d">' + esc(signed(z.vs_normal_f, "°F")) + " against the " + esc(String(z.normal_label || "").split(",")[0]) + "</span>" : "") + "</div>" + spark(temps, C.s1) + "</div>" +
      '<dl class="facts">' +
      "<dt>Break</dt><dd>" + (b ? esc([b.step_f + "°F over", b.over_nm, "nm, warm side", b.warm_side].join(" ")) : '<span class="mut">no break in the zone</span>') + "</dd>" +
      "<dt>Color</dt><dd>" + (z.chl != null ? esc([z.chl, "mg/m³ (" + colorWord(z.chl) + ")"].join(" ")) : '<span class="mut">cloud, no reading</span>') + "</dd>" +
      "<dt>Paddies</dt><dd>" + (p ? esc(p.word) + '<span class="mut"> · drift model</span>' : '<span class="mut">none in the model</span>') + "</dd>" +
      "<dt>Reports</dt><dd>" + zoneObs(z) + "</dd>" +
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

  function zoneObs(z) {
    var rows = obsRecent(["catch", "sighting", "dive"]).filter(function (o) { var w = zoneOf(o.lat, o.lng); return w && w.id === z.id; });
    if (!rows.length) return '<span class="mut">none in ' + OBS_DAYS + " days</span>";
    var counts = {};
    rows.forEach(function (o) {
      var keys = o.type === "catch" ? o.species || [] : o.type === "sighting" ? [o.sighting] : [visRange(o) + " ft vis"];
      keys.forEach(function (k) { counts[k] = (counts[k] || 0) + 1; });
    });
    var top = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).slice(0, 3)
      .map(function (k) { return counts[k] > 1 ? k + " (" + counts[k] + ")" : k; });
    return '<a href="#water">' + esc([rows.length, rows.length > 1 ? "reports" : "report"].join(" ")) + "</a>" + esc(": " + top.join(", ")) +
      '<div class="mut small">latest ' + esc(md(rows[0].date)) + "</div>";
  }

  function bindGps() { bindGpsIn(main); }
  function bindGpsIn(root) {
    Array.prototype.forEach.call(root.querySelectorAll("button[data-copy]"), function (btn) {
      btn.addEventListener("click", function () {
        var t = btn.getAttribute("data-copy");
        var done = function () { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1500); };
        if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, function () { /* clipboard blocked */ });
      });
    });
    Array.prototype.forEach.call(root.querySelectorAll("button[data-lat]"), function (btn) {
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

  // One line per zone; the full card opens under it.
  function zoneRow(z) {
    var f = z.fleet || {}, b = z["break"], p = z.paddy;
    var n = obsRecent(["catch", "sighting", "dive"]).filter(function (o) { var w = zoneOf(o.lat, o.lng); return w && w.id === z.id; }).length;
    var cells = [
      ["Water", z.temp_f != null ? esc(z.temp_f + "°F") + (z.temp_change_f != null ? ' <span class="d">' + esc(signed(z.temp_change_f, "")) + "</span>" : "") : '<span class="mut">n/a</span>'],
      ["vs normal", z.vs_normal_f != null ? esc(signed(z.vs_normal_f, "°F")) : ""],
      ["Break", b ? esc(b.step_f + "°F") : '<span class="mut">none</span>'],
      ["Paddies", p ? esc(p.word) : '<span class="mut">none</span>'],
      ["Fleet 3 d", esc((f.last3_hours || 0) + " h")],
      ["Reports", n ? esc(String(n)) : '<span class="mut">0</span>'],
    ];
    return '<details class="zrow" id="z-' + esc(z.id) + '"><summary><span class="zn">' + esc(zname(z)) + ' <span class="mut small">' + esc(z.nm_from_sd + " nm") + "</span></span>" +
      cells.map(function (c) { return '<span class="zc"><i>' + esc(c[0]) + "</i>" + c[1] + "</span>"; }).join("") + "</summary>" + zoneCard(z) + "</details>";
  }

  function zonesSection(groups) {
    var zs = facts.zones || [];
    var head = '<div class="zhead"><span class="zn">Zone</span><span class="zc">Water (6-day change)</span><span class="zc">vs normal</span><span class="zc">Break</span><span class="zc">Paddies</span><span class="zc">Fleet, 3 days</span><span class="zc">Reports</span></div>';
    return '<section><h2 id="zones">Zones</h2><p class="small mut">Tap a zone for its GPS, water line, color, fleet history, species that fit the water and the next days.</p>' +
      (facts.zone_groups || []).filter(function (g) { return groups.indexOf(g.key) >= 0; }).map(function (g) {
        return "<h3>" + esc(g.label) + "</h3>" + head + zs.filter(function (z) { return z.group === g.key; }).map(zoneRow).join("");
      }).join("") + '<p class="note">Water and breaks from the satellite map, color from chlorophyll, fleet from AIS (heard within about 30-50 nm of shore; most boats under 65 ft never broadcast). ' +
      '"Water fits" lists species whose usual range includes the zone\'s water this month. ' + esc((facts.fishing || {}).counts_note || "") + "</p></section>";
  }

  // A dive report belongs to the nearest dive site within 3 nm, so neighbouring
  // sites (Cove and Shores) don't both claim it.
  function nearestSite(o) {
    var best = null;
    (facts.dive_sites || []).forEach(function (s) {
      if (s.lat == null) return;
      var d = nm([o.lat, o.lng], [s.lat, s.lng]);
      if (d <= 3 && (!best || d < best.d)) best = { d: d, s: s };
    });
    return best ? best.s : null;
  }

  function diveSites() {
    var sites = facts.dive_sites || [];
    if (!sites.length) return "";
    return '<section><h2 id="sites">Site details</h2><details class="more"><summary>Sets for 4 days, water trend and surge at each site</summary><div class="zones">' + sites.map(function (s) {
      var vis = s.vis || [];
      var lastVis = vis.length ? vis[vis.length - 1] : null;
      var temps = (s.temp_series || []).map(function (t) { return t.temp_f; });
      var vp = s.vis_pred;
      var divers = obsRecent(["dive"]).filter(function (o) { return nearestSite(o) === s; });
      return '<article class="zone"><header><h3>' + esc(s.name) + '</h3></header><div class="mut small spots">' + esc(s.note) + "</div>" +
        (divers.length ? '<div class="row"><div class="metric"><span class="k">Divers reported</span><span class="v2">' + esc(visRange(divers[0]) + " ft") + "</span>" +
          '<span class="d">' + esc([md(divers[0].date), divers.length > 1 ? "· " + divers.length + " reports this week" : ""].join(" ")) + "</span></div></div>" : "") +
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
        (divers.length ? "" : '<dt>Divers</dt><dd><span class="mut">no reports this week</span> · ' + sendLink("send one") + "</dd>") +
        "</dl></article>";
    }).join("") + "</div></details></section>";
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
    var prim = [], more = [], first = true;
    var temp = lineChart({ title: "Water temperature at the buoys", sub: "daily mean, °F", unit: "°F", decimals: 0,
      series: bseries(view === "fishing" ? ["46254", "46232", "46086", "46047"] : ["46254", "46232", "46253"], "temp_f") });
    var VA = (facts.visibility || {}).areas || { "la-jolla": (facts.visibility || {}).calibration || {} };
    if (view !== "fishing") {
      Object.keys(VA).forEach(function (k) {
        var cal = VA[k];
        if (!cal.pairs || cal.pairs.length <= 5 || !cal.applied) return;
        var own = cal.pooled ? [cal.own_n, "reports here, too few for their own fit; scaled by the all-area", cal.scale]
          : [cal.pairs.length, "reports; prediction scaled by", cal.scale];
        var base = cal.mae_persist_ft != null ? "; off by " + [cal.mae_after_ft, "ft on average, against", cal.mae_persist_ft, "ft for repeating the previous post"].join(" ") : "";
        (first ? prim : more).push(lineChart({ title: "Predicted vs posted visibility, " + (cal.label || k), sub: own.join(" ") + base, unit: " ft",
          series: [{ name: "Post (range)", short: "Posted", color: C.s1, range: true, points: cal.pairs.map(function (p) { return { d: p.date, lo: p.obs_lo, hi: p.obs_hi }; }) },
                   { name: "Predicted", short: "Predicted", color: C.s2, points: cal.pairs.map(function (p) { return { d: p.date, v: p.pred_ft }; }) }] }));
        first = false;
      });
    }
    prim.push(temp);
    var vis = (t.vis || {})["dive-shop-justgetwet"];
    if (view !== "fishing" && vis && vis.length > 2) {
      more.push(lineChart({ title: "Visibility posted by Just Get Wet (La Jolla)", sub: "low to high of each post, ft", unit: " ft",
        refs: [{ v: 15, label: "15 ft" }],
        series: [{ name: "Visibility", color: C.s1, range: true, points: vis.map(function (p) { return { d: p.date, lo: p.lo_ft, hi: p.hi_ft }; }) }] }));
    }
    var gl = (t.glider || []).filter(function (g) { return g.mixed_layer_ft != null; });
    if (gl.length > 2) {
      more.push(lineChart({ title: "Depth of the warm top layer offshore", sub: "Spray glider nearest San Diego each day, ft, deeper is lower", unit: " ft", invert: true,
        series: [{ name: "Warm layer", color: C.s3, points: gl.map(function (g) { return { d: g.date, v: g.mixed_layer_ft }; }) }] }));
    }
    more.push(lineChart({ title: "Wave height at the buoys", sub: "daily mean, ft", unit: " ft", decimals: 1, series: bseries(["46232", "46086"], "wave_ft") }));
    if (view === "fishing" && (t.enso || []).length > 3) {
      (view === "fishing" ? prim : more).push(lineChart({ title: "El Niño: weekly Niño-3.4 anomaly", sub: "NOAA CPC, °C", unit: "°C", decimals: 1, zero: true,
        series: [{ name: "Niño-3.4", color: C.s2, points: t.enso.map(function (e) { return { d: e.date, v: e.nino34 }; }) }] }));
    }
    return '<section><h2 id="trends">Trends</h2>' + chartsBlock(prim.filter(Boolean), more.filter(Boolean)) +
      (t.archive_days ? '<p class="note">Our daily zone archive has ' + esc(t.archive_days) + " day(s) so far; zone trends lengthen as it grows.</p>" : "") + "</section>";
  }

  function seasonSection() {
    var sc = facts.season_curves;
    if (!sc || !(sc.buoys || []).length) return "";
    var yr = String(sc.year), ly = String(sc.last_year), dom = [yr + "-01-01", yr + "-12-31"];
    var ids = view === "fishing" ? ["46232", "46086", "46047"] : ["46254", "46232", "46253"];
    var prim = [], more = [];
    ids.forEach(function (id, i) {
      var b = sc.buoys.filter(function (x) { return x.id === id; })[0];
      if (!b) return;
      (i === 0 ? prim : more).push(lineChart({ title: "Water at " + (BUOY_SHORT[id] || b.name), sub: "weekly mean, °F", unit: "°F", decimals: 0, domain: dom, nowDate: facts.date, tipPrefix: "Week of ",
        series: [{ name: sc.range_label + " range", short: sc.range_label, color: "#94a3b8", band: true, points: b.weeks.map(function (w) { return { d: w.w, lo: w.lo, hi: w.hi }; }) },
                 { name: ly, color: C.s2, points: b.weeks.map(function (w) { return { d: w.w, v: w.last }; }) },
                 { name: yr, color: C.s1, points: b.weeks.map(function (w) { return { d: w.w, v: w["this"] }; }) }] }));
    });
    var fl = sc.fleet;
    if (view === "fishing" && fl) {
      (fl.groups || []).forEach(function (g, i) {
        (i === 0 ? prim : more).push(lineChart({ title: "Fleet hours stopped: " + g.label, sub: "per week, AIS-tracked boats", unit: " h", decimals: 0, domain: dom, nowDate: facts.date, tipPrefix: "Week of ",
          series: [{ name: ly, color: C.s2, points: g.weeks.map(function (w) { return { d: w.w, v: w.last }; }) },
                   { name: yr, color: C.s1, points: g.weeks.map(function (w) { return { d: w.w, v: w["this"] }; }) }] }));
      });
    }
    var out = '<section><h2 id="season">This season against last year</h2>' + chartsBlock(prim.filter(Boolean), more.filter(Boolean));
    if (view === "fishing") {
      var zs = (facts.zones || []).filter(function (z) { return z.vs_normal_f != null || (z.fleet || {}).season; });
      var lab = (zs.filter(function (z) { return z.normal_label; })[0] || {}).normal_label || "";
      if (zs.length) {
        out += '<details class="more"><summary>By zone: water against normal, fleet hours against last year</summary><div class="scroll"><table><thead><tr><th>Zone</th><th class="n">Water</th><th class="n">Against the ' + esc(lab) + '</th><th class="n">Fleet hours, recent weeks</th><th class="n">Same weeks ' + esc(ly) + "</th></tr></thead><tbody>" +
          zs.map(function (z) {
            var fs = (z.fleet || {}).season;
            return "<tr><td>" + esc(z.short || z.name) + '</td><td class="n">' + esc(z.temp_f != null ? z.temp_f + "°F" : "") + '</td><td class="n">' + esc(z.vs_normal_f != null ? signed(z.vs_normal_f, "°F") : "") +
              '</td><td class="n">' + esc(fs ? [fs.this_h, "h"].join(" ") : "") + '</td><td class="n">' + esc(fs ? [fs.last_h, "h"].join(" ") : "") + "</td></tr>";
          }).join("") + "</tbody></table></div></details>";
        var f0 = zs.map(function (z) { return (z.fleet || {}).season; }).filter(Boolean)[0];
        if (f0) out += '<p class="note">Fleet hours cover ' + esc(f0.weeks) + " complete week" + (f0.weeks > 1 ? "s" : "") + " since " + esc(md(f0.since)) +
          " that have AIS coverage in both years; the satellite water map is compared with NOAA's 1991-2020 average for the month (OISST, a coarser product, so a few tenths of a degree either way is noise).</p>";
      }
    }
    var gaps = ((fl || {}).gaps || []).map(function (g) { return [g.weeks, "weeks in", g.year].join(" "); });
    return out + '<p class="note">Buoy weekly means from NDBC; the band is the lowest to highest weekly mean of ' + esc(sc.range_label) + "." +
      (view === "fishing" && fl ? " Fleet hours are the same " + esc(fl.boats) + " boats both years; weeks without AIS coverage are breaks, not zeros" + (gaps.length ? " (" + esc(gaps.join(", ")) + ")" : "") + "." : "") + "</p></section>";
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
    var rows = obsRecent(["bait"]), latest = {}, n = {};
    rows.forEach(function (o) { n[o.barge] = (n[o.barge] || 0) + 1; if (!latest[o.barge]) latest[o.barge] = o; });
    var barges = OBS_BARGES.filter(function (k) { return latest[k]; });
    var table = barges.length ? '<div class="scroll"><table><thead><tr><th>Barge</th><th>Bait</th><th>Size</th><th>As of</th><th class="n">Reports</th></tr></thead><tbody>' +
      barges.map(function (k) {
        var o = latest[k];
        return "<tr><td>" + esc(k) + "</td><td>" + esc(o.bait) + (o.confidence === "confirmed" ? tagHtml("confirmed") : "") + "</td><td>" + esc(o.bait_size || "") +
          "</td><td>" + esc(md(o.date)) + '</td><td class="n">' + esc(n[k]) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
      : '<p class="small mut">No bait reports in the last ' + OBS_DAYS + " days. " + sendLink("Report what the barge had") + ".</p>";
    return '<section><h2 id="bait">Bait</h2>' + table + (b ? '<p class="small">' + esc(b.text) + " " + b.links.map(function (l) { return link(l.url, l.name); }).join("; ") + ".</p>" : "") + "</section>";
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
    if (view === "diving") return sectionNav() + glanceDiving() + mapSection() + hotSection(false) + speciesBoard() + waterSection() + weather() + trendsSection() + seasonSection() + diveSites() + around(true) + basics() + sourcesBlock();
    return sectionNav() + glanceFishing() + hotSection(true) + speciesBoard() + zonesSection(["offshore", "mexico", "inshore"]) + weather() + waterSection() + trendsSection() + seasonSection() + bait() + fleetSection() + around(false) + spotsTable() + sourcesBlock();
  }

  function render() {
    CHARTS = {}; chartSeq = 0;
    var tabs = '<div class="tabs" role="tablist" aria-label="Report view">' + VIEWS.map(function (v) {
      return '<button type="button" role="tab" aria-selected="' + (v[0] === view) + '" data-view="' + v[0] + '" class="' + (v[0] === view ? "on" : "") + '">' + v[1] + "</button>";
    }).join("") + "</div>";
    main.innerHTML = '<h1>Ocean Report</h1><p class="mut">' + esc(facts.date_label) + " · updated " + esc(facts.generated_local) + "</p>" + tabs + body();
    Array.prototype.forEach.call(main.querySelectorAll(".tabs button"), function (b) {
      b.addEventListener("click", function () {
        view = b.getAttribute("data-view");
        try { localStorage.setItem("sd:report:view", view); } catch (e) { /* storage blocked */ }
        history.replaceState(null, "", "#view=" + view);
        var y = window.scrollY; render(); window.scrollTo(0, y);
      });
    });
    bindCharts();
    // Charts inside a closed expander have no width yet; draw them when it opens.
    Array.prototype.forEach.call(main.querySelectorAll("details.more"), function (d) {
      d.addEventListener("toggle", function () { if (d.open) Array.prototype.forEach.call(d.querySelectorAll("figure.chart"), drawChart); });
    });
    initMap();
    bindGps();
    bindHot();
    bindForm();
    Array.prototype.forEach.call(main.querySelectorAll("a.send-open"), function (a) {
      a.addEventListener("click", function (ev) { ev.preventDefault(); openSend(); });
    });
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
      var feed = fetch("/api/reports/feed").then(function (r) { return r.ok ? r.json() : { reports: [] }; })
        .catch(function () { return { reports: [] }; });
      return Promise.all([load(want), feed]);
    })
    .then(function (res) {
      var f = res[0];
      if (!f.zones) throw new Error("older report format");
      facts = f;
      OBS = (res[1].reports || []).filter(function (o) { return o.lat != null && o.lng != null && o.date; });
      render();
      if (location.hash === "#send") openSend();
    })
    .catch(unavailable);
  var hdrSend = document.getElementById("send-link");
  if (hdrSend) hdrSend.addEventListener("click", function (ev) { ev.preventDefault(); openSend(); });
})();
