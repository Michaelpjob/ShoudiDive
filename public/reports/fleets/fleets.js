/* Fleet log: each landing's boats over the last week, from AIS (where they
   went, how long they held a spot, how far from the dock) and, where the
   landing posts its own counts, what they caught. Reads
   /data/reports/fleets.json (pipeline/reports/fleets.py). Every data string
   goes through esc() before it reaches innerHTML. */
(function () {
  "use strict";
  var main = document.getElementById("fleet");
  // Landing colors: the validated dark categorical slots, in a fixed order per region.
  var PAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"];
  var GAME = ["bluefin", "yellowfin", "yellowtail", "dorado", "wahoo", "striped marlin", "white seabass", "halibut", "calico bass", "lingcod"];
  var data = null, regionSel = initialRegion(), map = null, tracks = {};

  function initialRegion() {
    var m = /[#&]r=(sd|oc|la|vsb)\b/.exec(location.hash);
    if (m) return m[1];
    try { var v = localStorage.getItem("sd:report:region"); if (/^(sd|oc|la)$/.test(v || "")) return v; } catch (e) { /* storage blocked */ }
    return "sd";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function link(url, text) {
    var u = String(url || "");
    return /^https?:\/\//.test(u) ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(text) + "</a>" : esc(text);
  }
  function day(iso, weekday) {
    var d = new Date(String(iso).slice(0, 10) + "T12:00:00Z");
    return d.toLocaleDateString("en-US", weekday ? { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" });
  }
  function hm(min) {
    min = Math.round(min || 0);
    var h = Math.floor(min / 60), m = min % 60;
    return h ? [h, "h"].concat(m ? [m, "min"] : []).join(" ") : [m, "min"].join(" ");
  }
  function ddmPart(v) {
    v = Math.abs(v);
    var d = Math.floor(v), m = Math.round((v - d) * 60);
    if (m === 60) { d += 1; m = 0; }
    return d + " " + (m < 10 ? "0" : "") + m;
  }
  function ddm(lat, lng) { return ddmPart(lat) + " x " + ddmPart(lng); }
  function fishIcon(name) { return window.SDArt ? window.SDArt.fishIcon(name) : ""; }
  function rank(k) { return GAME.indexOf(k) < 0 ? 1 : 0; }
  function sortedFish(c) { return Object.keys(c || {}).filter(function (k) { return c[k] > 0; }).sort(function (a, b) { return rank(a) - rank(b) || c[b] - c[a]; }); }
  function catchChips(c, n) {
    return '<span class="fchips">' + sortedFish(c).slice(0, n || 4).map(function (k) {
      return '<span class="fchip">' + fishIcon(k) + esc([c[k].toLocaleString("en-US"), k].join(" ")) + "</span>";
    }).join("") + "</span>";
  }
  function catchText(c, n) { return sortedFish(c).slice(0, n || 4).map(function (k) { return [c[k].toLocaleString("en-US"), k].join(" "); }).join(", "); }
  function joinAnd(list) { return list.length < 2 ? list.join("") : list.slice(0, -1).join(", ") + " and " + list[list.length - 1]; }

  function regionInfo() { return (data.regions || []).filter(function (r) { return r.id === regionSel; })[0] || data.regions[0]; }
  function regionLandings() { return (data.landings || []).filter(function (L) { return L.region === regionSel; }); }
  function colorOf(L) { var i = regionLandings().indexOf(L); return PAL[(i < 0 ? 0 : i) % PAL.length]; }
  function heardWell(tr) { return tr.heard && tr.cov != null && tr.cov >= 0.5; }

  // --- one trip, in words --------------------------------------------------------
  function tripWhen(tr) {
    var parts = [day(tr.date, true)];
    if (!tr.heard) parts.push("not heard by AIS");
    else if (heardWell(tr) && tr.dep && tr.ret) parts.push([tr.dep, "to", tr.ret, ["(", tr.hours, " h)"].join("")].join(" "));
    else parts.push("partly heard by AIS");
    if (tr.heard && tr.max_nm) parts.push(["out to", tr.max_nm.toFixed(0), "nm from the dock"].join(" "));
    return parts.join(" · ");
  }
  function tripWhere(tr) {
    var bits = tr.places.map(function (pl) { return [pl.place, hm(pl.minutes)].join(" "); });
    if (tr.near_min) bits.push(hm(tr.near_min) + " close to shore");
    if (!tr.heard) return "";
    return bits.length ? "Stopped: " + bits.join("; ") : "No stops heard";
  }
  function tripRow(L, b, tr, key) {
    var meta = [tr.trip, tr.anglers ? tr.anglers + " anglers" : ""].filter(Boolean).join(", ");
    var hasLine = tr.track && tr.track.length > 1;
    return '<li class="trip' + (tr.heard ? "" : " unheard") + '"><div class="tw">' + esc(tripWhen(tr)) + "</div>" +
      (tripWhere(tr) ? '<div class="small">' + esc(tripWhere(tr)) + "</div>" : "") +
      (tr.catch && Object.keys(tr.catch).length ? '<div class="small">' + catchChips(tr.catch, 4) + (meta ? ' <span class="mut">' + esc(meta) + "</span>" : "") + "</div>" : "") +
      (hasLine ? '<button type="button" class="mini-btn" data-trip="' + esc(key) + '">Show on map</button>' : "") + "</li>";
  }

  // --- a landing's week -------------------------------------------------------
  function landingSummary(L) {
    var out = [[L.n_trips, L.n_trips === 1 ? "trip" : "trips", "by", L.n_boats, L.n_boats === 1 ? "boat" : "boats", "this week"].join(" ") +
      (L.n_heard < L.n_trips ? ["; AIS heard", L.n_heard, "of them."].join(" ") : ".")];
    if (L.top_places.length) out.push("Most time on spots: " + L.top_places.slice(0, 2).map(function (pl) { return [pl.place, "(" + hm(pl.minutes) + ",", pl.trips, pl.trips === 1 ? "trip)" : "trips)"].join(" "); }).join("; ") + ".");
    if (L.farthest) out.push(["Farthest from the dock:", L.farthest.nm.toFixed(0), "nm,", L.farthest.boat, "on", day(L.farthest.date) + "."].join(" "));
    return out;
  }
  function landingHtml(L) {
    var col = colorOf(L), lines = landingSummary(L);
    var catchHtml = L.counts
      ? (Object.keys(L.catch).length ? '<div class="fl-catch">' + catchChips(L.catch, 6) + '</div><p class="note">Catch from ' + link(L.counts.url, L.counts.landing) + "'s own counts.</p>" : "")
      : '<p class="note">' + esc(["Catches not shown:", L.name, "posts its counts only through a service that does not allow reuse."].join(" ")) + "</p>";
    var boats = L.boats.filter(function (b) { return b.trips.length; }).map(function (b, bi) {
      var total = {};
      b.trips.forEach(function (tr) { Object.keys(tr.catch || {}).forEach(function (k) { total[k] = (total[k] || 0) + tr.catch[k]; }); });
      var sub = [b.length_ft ? b.length_ft + " ft" : "", [b.trips.length, b.trips.length === 1 ? "trip" : "trips"].join(" ")].filter(Boolean).join(" · ");
      return '<details class="boat"' + (bi < 2 ? " open" : "") + "><summary><b>" + esc(b.name) + '</b> <span class="mut small">' + esc(sub) + "</span>" +
        (Object.keys(total).length ? catchChips(total, 3) : "") + '</summary><ul class="plain trips">' +
        b.trips.map(function (tr, ti) { return tripRow(L, b, tr, [L.name, b.name, ti].join("|")); }).join("") + "</ul></details>";
    }).join("");
    return '<section class="fl-landing"><h2><i class="sw" style="background:' + col + '"></i>' + esc(L.name) +
      ' <span class="sub">' + esc(L.port) + "</span></h2>" + '<p class="fl-sum">' + esc(lines.join(" ")) + "</p>" + catchHtml + boats + "</section>";
  }

  // --- page -------------------------------------------------------------------
  function render() {
    var R = regionInfo(), Ls = regionLandings(), active = Ls.filter(function (L) { return L.n_trips; }), quiet = Ls.filter(function (L) { return !L.n_trips; });
    var chips = '<div class="regions" role="group" aria-label="Region">' + data.regions.map(function (r) {
      return '<button type="button" class="chip' + (r.id === regionSel ? " on" : "") + '" data-region="' + esc(r.id) + '" aria-pressed="' + (r.id === regionSel) + '">' + esc(r.name) + "</button>";
    }).join("") + "</div>";
    var stats = [[String(R.n_trips), R.n_trips === 1 ? "trip" : "trips", R.n_heard < R.n_trips ? [R.n_heard, "from AIS,", R.n_trips - R.n_heard, "posted by landings"].join(" ") : "from AIS"],
      [String(R.n_boats), R.n_boats === 1 ? "boat out" : "boats out", [R.n_active, "of", R.n_landings, "landings"].join(" ")],
      [hm(R.offshore_min), "on offshore spots", "time boats held a spot"],
      [R.farthest ? R.farthest.nm.toFixed(0) + " nm" : "none", "farthest from a dock", R.farthest ? [R.farthest.boat, day(R.farthest.date)].join(", ") : ""]];
    var cards = sortedFish(R.catch).slice(0, 4);
    var html = "<h1>Fleet log</h1>" +
      '<p class="mut">' + esc([day(data.since), "to", day(data.until) + " · where each landing's boats went, from AIS, and what they caught where the landing posts its own counts · updated", data.generated_local].join(" ")) + "</p>" +
      chips + '<div class="stats four">' + stats.map(function (st) { return '<div class="st"><b>' + esc(st[0]) + "</b><span>" + esc(st[1]) + '</span><em>' + esc(st[2]) + "</em></div>"; }).join("") + "</div>" +
      (cards.length ? '<section class="brief"><h2>Posted catch this week <span class="sub">' + esc(joinAnd(active.filter(function (L) { return L.counts; }).map(function (L) { return L.counts.landing; }))) + '</span></h2><div class="catch n' + String(cards.length) + '">' +
        cards.map(function (k) { return '<div class="cc">' + fishIcon(k) + "<b>" + esc(R.catch[k].toLocaleString("en-US")) + "</b><span>" + esc(k) + "</span></div>"; }).join("") + "</div></section>" : "") +
      (active.length ? '<div id="flmap" class="map bmap" role="region" aria-label="Map of this week\'s trips by landing"></div><div class="maplegend">' +
        active.map(function (L) { return '<span class="key"><i style="background:' + colorOf(L) + '"></i>' + esc(L.name) + "</span>"; }).join("") +
        '<span class="key"><i class="stopdot"></i>held a spot (size = time)</span></div>' : "") +
      (active.length ? active.map(landingHtml).join("") : '<p class="small mut">' + esc(["No trips heard from", R.name, "landings this week."].join(" ")) + "</p>") +
      (quiet.length ? '<p class="small mut">' + esc("No trips heard this week: " + joinAnd(quiet.map(function (L) { return L.name; })) + ".") + "</p>" : "") +
      '<section><h2>How to read this</h2><p class="small">' + esc("Positions come from AIS, the boats' own transponders, as heard by shore receivers. Most boats under 65 ft carry no AIS, and receivers lose a boat 30-50 nm out, so a trip can be heard in part or not at all. When AIS heard less than half a trip, its times are left out. A spot is where a boat held still offshore; time inside the nearshore band is one total. Distances are straight-line from the landing's dock.") +
      "</p><p class=\"small\">" + esc("Catch comes only from landings that post their own counts (Dana Wharf, Davey's Locker), joined to the boat's AIS trip that day; a posted trip AIS did not hear is listed without a track. The other landings publish counts only through a service that does not allow reuse.") +
      "</p><p class=\"small\"><a href=\"/reports/\">Back to the Ocean Report</a></p></section>";
    main.innerHTML = html;
    Array.prototype.forEach.call(main.querySelectorAll("button[data-region]"), function (b) {
      b.addEventListener("click", function () {
        regionSel = b.getAttribute("data-region");
        try { if (regionSel !== "vsb") localStorage.setItem("sd:report:region", regionSel); } catch (e) { /* storage blocked */ }
        history.replaceState(null, "", "#r=" + regionSel);
        render();
      });
    });
    Array.prototype.forEach.call(main.querySelectorAll("button[data-trip]"), function (b) {
      b.addEventListener("click", function () { showTrip(b.getAttribute("data-trip")); });
    });
    initMap(R, active);
  }

  function initMap(R, active) {
    var el = document.getElementById("flmap");
    if (map) { map.remove(); map = null; }
    tracks = {};
    if (!el || !window.L) return;
    map = L.map(el, { zoomControl: true, attributionControl: false, scrollWheelZoom: false, zoomSnap: 0.25, zoomDelta: 0.5, dragging: !L.Browser.mobile, tap: false });
    gestureHandling(map, el);
    var land = L.layerGroup().addTo(map), lines = L.layerGroup().addTo(map), dots = L.layerGroup().addTo(map);
    fetch("/data/land.geojson").then(function (r) { return r.ok ? r.json() : null; }).then(function (g) {
      if (g) L.geoJSON(g, { style: { color: "#475569", weight: 0.6, fillColor: "#1f2937", fillOpacity: 1 }, interactive: false }).addTo(land);
    }).catch(function () { /* map still useful without land */ });
    var bounds = L.latLngBounds(R.box);
    active.forEach(function (Lg) {
      var col = colorOf(Lg);
      Lg.boats.forEach(function (b) {
        b.trips.forEach(function (tr, ti) {
          if (!tr.track || tr.track.length < 2) return;
          var key = [Lg.name, b.name, ti].join("|");
          var pop = "<b>" + esc(b.name) + '</b> <span class="mut">' + esc(Lg.name) + "</span><div>" + esc(tripWhen(tr)) + "</div>" +
            (tripWhere(tr) ? '<div class="small">' + esc(tripWhere(tr)) + "</div>" : "") +
            (tr.catch && Object.keys(tr.catch).length ? '<div class="small">' + esc(catchText(tr.catch, 4)) + "</div>" : "");
          var line = L.polyline(tr.track, { color: col, weight: 2, opacity: 0.75 }).bindPopup(pop, { maxWidth: 260 }).addTo(lines);
          tracks[key] = line;
          tr.track.forEach(function (pt) { bounds.extend(pt); });
          tr.places.forEach(function (pl) {
            L.circleMarker([pl.lat, pl.lng], { radius: Math.max(4, Math.min(12, Math.sqrt(pl.minutes) * 1.1)), color: "#0b1220", weight: 1.5, fillColor: col, fillOpacity: 0.95 })
              .bindPopup("<b>" + esc(pl.place) + "</b><div>" + esc([b.name + ",", day(tr.date) + ":", "held", hm(pl.minutes)].join(" ")) + '</div><div class="small"><span class="gps">' + esc(ddm(pl.lat, pl.lng)) + "</span></div>", { maxWidth: 240 })
              .addTo(dots);
          });
        });
      });
    });
    map.fitBounds(bounds, { padding: [10, 10], maxZoom: 10 });
  }
  function showTrip(key) {
    var line = tracks[key];
    if (!line || !map) return;
    Object.keys(tracks).forEach(function (k) { tracks[k].setStyle({ weight: 2, opacity: k === key ? 1 : 0.25 }); });
    line.setStyle({ weight: 4, opacity: 1 }).bringToFront();
    document.getElementById("flmap").scrollIntoView({ behavior: "smooth", block: "center" });
    map.flyToBounds(line.getBounds(), { padding: [24, 24], maxZoom: 11, duration: 0.7 });
    setTimeout(function () { line.openPopup(); }, 750);
  }
  // Same as the report's map: the page scrolls past the map; Ctrl/Cmd + wheel zooms; two fingers move it on a phone.
  function gestureHandling(m, el) {
    var hint = document.createElement("div"), timer = null, acc = 0;
    hint.className = "map-hint"; hint.hidden = true; el.appendChild(hint);
    var say = function (msg) { hint.textContent = msg; hint.hidden = false; clearTimeout(timer); timer = setTimeout(function () { hint.hidden = true; }, 1300); };
    el.addEventListener("wheel", function (ev) {
      if (ev.ctrlKey || ev.metaKey) {
        ev.preventDefault();
        acc += -ev.deltaY / 120;
        if (Math.abs(acc) >= 0.25) { m.setZoomAround(m.mouseEventToContainerPoint(ev), m.getZoom() + Math.max(-1, Math.min(1, acc))); acc = 0; }
      } else {
        say(/Mac|iPhone|iPad/.test(navigator.platform || "") ? "Hold Cmd and scroll to zoom the map" : "Hold Ctrl and scroll to zoom the map");
      }
    }, { passive: false });
    if (L.Browser.mobile) el.addEventListener("touchmove", function (ev) { if (ev.touches.length === 1) say("Use two fingers to move the map"); }, { passive: true });
  }

  fetch("/data/reports/fleets.json", { cache: "no-cache" }).then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }).then(function (d) {
    if (!d.regions || !d.landings) throw new Error("no fleet log");
    data = d;
    render();
  }).catch(function () {
    main.innerHTML = '<h1>Fleet log</h1><p>The fleet log is in preview and is not published on this site yet.</p><p><a href="/reports/">Back to the Ocean Report</a></p>';
  });
})();
