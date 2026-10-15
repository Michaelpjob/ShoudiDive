/* Morning Report: renders /data/reports/<date>.json (built by
   pipeline/reports/build_report.py). Every string from the data goes through
   esc(); the page adds no numbers of its own. */
(function () {
  "use strict";
  var DATA = "/data/reports/";
  var main = document.getElementById("report");
  var picker = document.getElementById("date");
  var facts = null;
  var dayKey = "today";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function chip(level, label) {
    return '<span class="chip ' + esc(level) + '">' + esc(label) + "</span>";
  }
  function tag(t) { return t ? '<span class="tag">' + esc(t) + "</span>" : ""; }
  function link(url, text) {
    if (!url) return esc(text);
    var u = String(url);
    if (!/^https?:\/\//.test(u) && u.charAt(0) !== "/") return esc(text);
    return '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(text) + "</a>";
  }
  function fmtTrain(t) {
    if (!t || t.ft == null) return "";
    return t.ft + " ft" + (t.s ? " at " + Math.round(t.s) + " s" : "") + (t.dir ? " " + t.dir : "");
  }
  function localTime(iso) {
    if (!iso) return "";
    var d = new Date(iso.replace(/Z$/, ":00Z"));
    if (isNaN(d)) return iso;
    return d.toLocaleString("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function srcLabel(id) {
    var s = (facts.sources || []).filter(function (x) { return x.id === id; })[0];
    return s ? s.label + (s.time_utc ? ", " + localTime(s.time_utc) : "") : id || "";
  }

  function verdictList(items, key) {
    return '<ul class="verdicts">' + items.map(function (it) {
      var d = it.days[key];
      if (!d) return "";
      var fs = (d.factors || []).map(function (f) {
        return '<div class="f">' + chip(f.level, facts.can_i_go.levels[f.level]) + " <b>" + esc(f.label) + "</b>" + tag(f.tag) +
          "<div>" + esc(f.value) + "</div>" +
          (f.rule ? '<div class="rule">Rule: ' + esc(f.rule) + "</div>" : "") +
          (f.source ? '<div class="rule">Source: ' + esc(srcLabel(f.source)) + "</div>" : "") + "</div>";
      }).join("");
      return "<li><details><summary><span class=\"name\">" + esc(it.name) + "</span>" + chip(d.level, d.label) +
        '<span class="why">' + esc(d.why) + "</span></summary>" +
        (fs ? '<div class="factors">' + fs + (it.note ? '<p class="note">' + esc(it.note) + "</p>" : "") + "</div>" : "") +
        "</details></li>";
    }).join("") + "</ul>";
  }

  function canIGo() {
    var c = facts.can_i_go;
    var days = facts.days.map(function (d) {
      return '<button type="button" data-day="' + esc(d.key) + '" class="' + (d.key === dayKey ? "on" : "") + '">' + esc(d.label) + "</button>";
    }).join("");
    var legend = ["within", "marginal", "outside", "unknown"].map(function (k) { return chip(k, c.levels[k]); }).join("");
    var heads = (c.headlines || []).length ? "<h3>NWS headlines in effect</h3><ul class=\"plain\">" + c.headlines.map(function (h) {
      return "<li><b>" + esc(h.event) + "</b>" + (h.until ? " until " + esc(h.until) : "") + (h.from ? " (from " + esc(h.from) + ")" : "") +
        '<div class="mut small">' + esc(h.area) + "</div></li>";
    }).join("") + "</ul>" : "";
    var bd = c.boat_dive.days[dayKey];
    return "<h2 id=\"go\">Can I go</h2>" +
      '<p class="mut small">Each verdict is the worst of the factors listed under it. Tap a row for the factors, the rule applied and the source. ' +
      "Thresholds are ours, not standards, and they cannot see your experience, your boat or conditions at depth.</p>" +
      '<div class="legend">' + legend + "</div>" +
      '<div class="days" role="group" aria-label="Day">' + days + "</div>" +
      "<h3>Shore dive</h3>" + verdictList(c.shore, dayKey) +
      "<h3>Private boat, by range</h3>" + verdictList(c.boat, dayKey) +
      "<h3>Boat dive</h3><ul class=\"verdicts\"><li><details><summary><span class=\"name\">" + esc(c.boat_dive.name) + "</span>" +
      chip(bd.level, bd.label) + '<span class="why">' + esc(bd.why) + "</span></summary><div class=\"factors\">" + esc(c.boat_dive.current) +
      "</div></details></li></ul>" +
      "<h3>Freediving</h3><p class=\"small\">" + esc(c.freedive) + "</p>" + heads;
  }

  function water() {
    var w = facts.water;
    var out = "<h2 id=\"water\">The water</h2>";
    var s = w.surf || {};
    var sd = s[dayKey];
    if (sd) {
      out += "<h3>Surf forecast, " + esc(s.county) + tag("Rule") + "</h3><dl class=\"kv\">" +
        "<dt>Surf</dt><dd>" + esc(sd.surf_text || "n/a") + (sd.swell_from_deg ? ", swell from " + esc(sd.swell_from_deg) + "°" : "") + "</dd>" +
        "<dt>Rip current risk</dt><dd>" + esc(sd.rip_risk || "n/a") + "</dd>" +
        (sd.water_temp_lo_f ? "<dt>Surf-zone water</dt><dd>" + esc(sd.water_temp_lo_f) + "-" + esc(sd.water_temp_hi_f) + "°F</dd>" : "") +
        "<dt>Thunderstorms</dt><dd>" + esc(sd.thunder || "n/a") + "</dd></dl>";
    }
    out += "<h3>Buoys" + tag("Observed") + '</h3><div class="scroll"><table><thead><tr><th>Buoy</th><th>Time</th><th class="n">Waves</th><th>Swell</th><th class="hide-sm">Wind waves</th><th class="n">Water</th></tr></thead><tbody>' +
      w.buoys.map(function (b) {
        if (b.status !== "ok") return "<tr><td>" + esc(b.name) + '</td><td colspan="5" class="mut">' + esc(b.status === "stale" ? "Reading too old to use" : "No reading") + "</td></tr>";
        return "<tr><td>" + esc(b.name) + (b.wind ? '<div class="mut small">Wind ' + esc(b.wind.kt) + " kt" + (b.wind.gust_kt ? ", gusts " + esc(b.wind.gust_kt) : "") + "</div>" : "") +
          "</td><td>" + esc(b.observed) + '</td><td class="n">' + esc(b.wave_ft != null ? b.wave_ft + " ft" : "") + "</td><td>" + esc(fmtTrain(b.swell)) +
          '</td><td class="hide-sm">' + esc(fmtTrain(b.wind_waves)) + '</td><td class="n">' + esc(b.temp_f != null ? b.temp_f + "°F" : "") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    if (w.piers && w.piers.length) {
      out += '<p class="small">' + w.piers.map(function (p) { return esc(p.name) + ": " + esc(p.temp_f) + "°F at " + esc(p.observed); }).join(". ") + "." + tag("Observed") + "</p>";
    }
    out += "<h3>Satellite maps</h3><p class=\"small\">";
    out += w.sst_field ? "Sea-surface temperature map: " + esc(w.sst_field.date_label) + " (" + esc(w.sst_field.age_days) + " days old; " + esc(w.sst_field.source) + "). " : "Sea-surface temperature map: not available. ";
    out += w.chl ? "Chlorophyll (water color): " + esc(w.chl.date_label) + ", " + esc(w.chl.coverage_pct) + "% of the area seen through clouds." : "";
    out += ' <a href="/">Open the map</a>.</p>';
    if (w.thermocline && w.thermocline.spots.length) {
      out += "<h3>Thermocline" + tag("Modeled") + "</h3><p class=\"small\">" + w.thermocline.spots.map(function (t) {
        return esc(t.name) + ": " + (t.no_cliff ? "no thermocline in the column" : "about " + esc(t.cliff_ft) + " ft");
      }).join("; ") + '.</p><p class="note">' + esc(w.thermocline.method) + "</p>";
    }
    var tide = (w.tide.events || []).map(function (e) { return esc(e.type) + " " + esc(e.ft) + " ft at " + esc(e.time); }).join(", ");
    out += "<h3>Tide, sun and moon</h3><dl class=\"kv\">" +
      (tide ? "<dt>" + esc(w.tide.station) + " tide</dt><dd>" + tide + "</dd>" : "") +
      "<dt>Sun</dt><dd>Up " + esc(w.sun.rise) + ", down " + esc(w.sun.set) + "</dd>" +
      "<dt>Moon</dt><dd>" + esc(w.moon.phase) + ", " + esc(w.moon.illuminated_pct) + "% lit</dd>" +
      (w.enso ? "<dt>El Niño</dt><dd>" + esc(w.enso.status) + (w.enso.nino34_c != null ? ", Niño-3.4 at " + (w.enso.nino34_c > 0 ? "+" : "") + esc(w.enso.nino34_c) + "°C" : "") +
        '<div class="mut small">' + esc(w.enso.synopsis) + " (NOAA CPC, " + esc(w.enso.issued) + ")</div></dd>" : "") + "</dl>";
    out += "<h3>Marine forecasts, verbatim" + tag("Rule") + "</h3>" + w.marine_text.map(function (z) {
      if (z.status !== "ok") return '<p class="small mut">' + esc(z.zone) + " " + esc(z.name) + ": not available.</p>";
      var d = dayKey === "today" ? z.today : z.tomorrow;
      return '<details class="raw"><summary><b>' + esc(z.zone) + "</b> " + esc(z.name) + "</summary>" +
        (z.headlines.length ? "<p><b>" + esc(z.headlines.join(". ")) + "</b></p>" : "") +
        "<p>" + esc(d || "No period for this day.") + "</p><p class=\"mut\">Issued " + esc(z.issued) + ". " + link(z.url, "Full product") + "</p></details>";
    }).join("");
    return out;
  }

  function divers() {
    var d = facts.divers;
    var out = "<h2 id=\"divers\">Divers' corner</h2><h3>Visibility reports" + tag("Reported") + "</h3>";
    out += d.posts.length ? '<ul class="plain">' + d.posts.map(function (p) {
      return "<li><b>" + esc(p.vis) + "</b> at " + esc(p.site) + ", " + link(p.url, p.source) + ", " + esc(p.date_label) +
        (p.days_ago ? " (" + esc(p.days_ago) + (p.days_ago === 1 ? " day" : " days") + " ago)" : " (today)") + "</li>";
    }).join("") + "</ul>" : '<p class="small mut">No dive-shop visibility report in the last three days.</p>';
    if (d.stale_sources.length) out += '<p class="note">Not shown because their posts have not changed in days: ' + esc(d.stale_sources.join(", ")) + ".</p>";
    if (d.surge.sites.length) {
      out += "<h3>Surge at depth" + tag("Modeled") + '</h3><div class="scroll"><table><thead><tr><th>Site</th><th class="n">Depth</th><th class="n">Water speed at the bottom</th><th>Reading</th></tr></thead><tbody>' +
        d.surge.sites.map(function (s) {
          return "<tr><td>" + esc(s.site) + '</td><td class="n">' + esc(s.depth_ft) + ' ft</td><td class="n">' + esc(s.u_ft_s) + " ft/s</td><td>" +
            esc(s.level === "strong" ? "Strong: expect a stirred-up bottom" : s.level === "moderate" ? "Moderate: fine sand lifting" : "Light") + "</td></tr>";
        }).join("") + '</tbody></table></div><p class="note">' + esc(d.surge.method) + "</p>";
    }
    out += "<h3>Water quality</h3><p class=\"small\">";
    if (d.rain) out += "Rain in the last 72 hours at " + esc(d.rain.gauge) + ": " + esc(d.rain.inches.toFixed(2)) + " in" +
      (d.rain.advisory ? ". The county advises against ocean contact for 72 hours after rain." : ".") + tag("Observed") + " ";
    out += esc(d.water_quality.text) + " " + link(d.water_quality.url, "San Diego County beach status") + ".</p>";
    out += "<h3>In season" + tag("Rule") + '</h3><ul class="plain">' + d.season.map(function (s) {
      return "<li><b>" + esc(s.label) + ":</b> " + esc(s.text) + ' <span class="mut small">(' + link(s.source, "CDFW") + ", checked " + esc(s.verified_on) + ")</span></li>";
    }).join("") + "</ul>";
    return out;
  }

  function sourcesBlock() {
    return "<h2 id=\"sources\">What this report doesn't cover</h2><ul class=\"plain\">" +
      facts.not_covered.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>" +
      '<h3>Sources</h3><div class="scroll"><table><thead><tr><th>Input</th><th>Status</th><th>Time (Pacific)</th></tr></thead><tbody>' +
      facts.sources.map(function (s) {
        return "<tr><td>" + link(s.url, s.label) + '</td><td class="status-' + esc(s.status) + '">' + esc(s.status) + "</td><td>" +
          esc(localTime(s.time_utc)) + (s.age_h != null ? ' <span class="mut">(' + esc(s.age_h) + " h)</span>" : "") + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      "<footer><p>Tags: " + Object.keys(facts.method.tags).map(function (k) { return "<b>" + esc(k) + "</b> = " + esc(facts.method.tags[k]); }).join("; ") +
      ".</p><p>This report rates conditions against stated thresholds. It is not a forecast of what you will find, and it does not replace your own look at the water. Lifeguards, harbor patrol and the captain on scene have the final say.</p></footer>";
  }

  function render() {
    main.innerHTML =
      "<h1>Morning Report</h1><p class=\"mut\">" + esc(facts.date_label) + ". Built " + esc(facts.generated_local) + " from " +
      facts.sources.filter(function (s) { return s.status === "ok"; }).length + " of " + facts.sources.length + ' live inputs (<a href="#sources">list</a>).</p>' +
      '<div class="bottom">' + facts.bottom_line.map(function (l) { return "<p>" + esc(l) + "</p>"; }).join("") + "</div>" +
      canIGo() + water() + divers() + sourcesBlock();
    Array.prototype.forEach.call(main.querySelectorAll(".days button"), function (b) {
      b.addEventListener("click", function () {
        dayKey = b.getAttribute("data-day");
        var y = window.scrollY;
        render();
        window.scrollTo(0, y);
      });
    });
  }

  function load(date) {
    return fetch(DATA + (date ? date : "latest") + ".json", { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function unavailable() {
    main.innerHTML = "<h1>Morning Report</h1><p>The daily report is in preview and is not published on this site yet.</p>" +
      '<p><a href="/">Back to the map</a></p>';
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
    .then(function (f) { facts = f; render(); })
    .catch(unavailable);
})();
