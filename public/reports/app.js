/* Morning Report: renders /data/reports/<date>.json (built by
   pipeline/reports/build_report.py) for three readers: fishing, diving,
   spearfishing. Every string from the data goes through esc(); the page adds
   no numbers of its own. */
(function () {
  "use strict";
  var DATA = "/data/reports/";
  var VIEWS = [["fishing", "Fishing"], ["diving", "Diving"], ["spearfishing", "Spearfishing"]];
  var main = document.getElementById("report");
  var picker = document.getElementById("date");
  var facts = null;
  var dayKey = "today";
  var view = initialView();

  function initialView() {
    var m = /view=(fishing|diving|spearfishing)/.exec(location.hash);
    if (m) return m[1];
    try { var v = localStorage.getItem("sd:report:view"); if (v) return v; } catch (e) { /* storage blocked */ }
    return "diving";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function chip(level, label) { return '<span class="chip ' + esc(level) + '">' + esc(label) + "</span>"; }
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
    var s = String(iso);
    var d = new Date(/T\d{2}:\d{2}Z$/.test(s) ? s.replace(/Z$/, ":00Z") : s);
    if (isNaN(d)) return iso;
    return d.toLocaleString("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function srcLabel(id) {
    var s = (facts.sources || []).filter(function (x) { return x.id === id; })[0];
    return s ? s.label + (s.time_utc ? ", " + localTime(s.time_utc) : "") : id || "";
  }
  function levelLabel(k) { return facts.can_i_go.levels[k] || k; }

  // --- Evidence under a verdict -------------------------------------------------
  function evidenceBlock(list) {
    if (!list || !list.length) return "";
    return '<div class="evidence"><div class="ev-head">The data behind this</div>' + list.map(function (b) {
      var head = "<b>" + esc(b.label) + "</b>" + tag(b.tag);
      if (b.kind === "series") {
        return '<div class="ev">' + head + (b.summary ? '<div class="small">' + esc(b.summary) + "</div>" : "") +
          '<div class="scroll"><table class="mini"><tbody>' + (b.rows || []).map(function (r) {
            return "<tr><td>" + esc(r.time) + "</td><td>" + (r.level ? chip(r.level, levelLabel(r.level)) + " " : "") + esc(r.text) + "</td></tr>";
          }).join("") + "</tbody></table></div></div>";
      }
      if (b.kind === "text") return '<div class="ev">' + head + '<blockquote>' + esc(b.text) + "</blockquote></div>";
      return '<div class="ev">' + head + "<div>" + esc(b.text) + "</div></div>";
    }).join("") + "</div>";
  }

  function verdictList(items, key) {
    return '<ul class="verdicts">' + items.map(function (it) {
      var d = it.days[key];
      if (!d) return "";
      var fs = (d.factors || []).map(function (f) {
        return '<div class="f">' + chip(f.level, levelLabel(f.level)) + " <b>" + esc(f.label) + "</b>" + tag(f.tag) +
          "<div>" + esc(f.value) + "</div>" +
          (f.rule ? '<div class="rule">Rule: ' + esc(f.rule) + "</div>" : "") +
          (f.source ? '<div class="rule">Source: ' + esc(srcLabel(f.source)) + "</div>" : "") + "</div>";
      }).join("");
      var period = d.period ? '<p class="note">NWS period used: ' + esc(d.period) + (d.period_fallback ? " (the daytime period has expired, so tonight's forecast covers the rest of today)" : "") + ".</p>" : "";
      return "<li><details><summary><span class=\"name\">" + esc(it.name) + "</span>" + chip(d.level, d.label) +
        '<span class="why">' + esc(d.why) + "</span></summary>" +
        '<div class="factors">' + fs + period + (it.note ? '<p class="note">' + esc(it.note) + "</p>" : "") + "</div>" +
        evidenceBlock(d.evidence) + "</details></li>";
    }).join("") + "</ul>";
  }

  function dayButtons() {
    return '<div class="days" role="group" aria-label="Day">' + facts.days.map(function (d) {
      return '<button type="button" data-day="' + esc(d.key) + '" class="' + (d.key === dayKey ? "on" : "") + '">' + esc(d.label) + "</button>";
    }).join("") + "</div>";
  }
  function legend() {
    return '<div class="legend">' + ["within", "marginal", "outside", "unknown"].map(function (k) { return chip(k, levelLabel(k)); }).join("") + "</div>";
  }
  function headlines() {
    var h = facts.can_i_go.headlines || [];
    if (!h.length) return "";
    return "<h3>NWS headlines in effect</h3><ul class=\"plain\">" + h.map(function (a) {
      return "<li><b>" + esc(a.event) + "</b>" + (a.until ? " until " + esc(a.until) : "") + (a.from ? " (from " + esc(a.from) + ")" : "") +
        '<div class="mut small">' + esc(a.area) + "</div>" +
        (a.description ? '<details class="raw"><summary>What NWS says</summary><p>' + esc(a.description) + "</p>" +
          (a.instruction ? "<p><b>" + esc(a.instruction) + "</b></p>" : "") + "</details>" : "") + "</li>";
    }).join("") + "</ul>";
  }

  // --- Sections ------------------------------------------------------------------
  function canIGo(order) {
    var c = facts.can_i_go;
    var parts = {
      shore: "<h3>Shore dive</h3>" + verdictList(c.shore, dayKey),
      boat: "<h3>Private boat, by range</h3>" + verdictList(c.boat, dayKey),
      boatdive: "<h3>Boat dive</h3>" + verdictList([{ name: c.boat_dive.name, days: c.boat_dive.days, note: c.boat_dive.current }], dayKey),
      freedive: "<h3>Freediving</h3><p class=\"small\">" + esc(c.freedive) + "</p>",
    };
    return "<h2 id=\"go\">Can I go</h2>" +
      '<p class="mut small">Each verdict is the worst of the factors under it. Tap a row for the factors, the rule, and the data behind it. ' +
      "Thresholds are ours, not standards, and cannot see your experience, your boat or conditions at depth.</p>" +
      legend() + dayButtons() + order.map(function (k) { return parts[k]; }).join("") + headlines();
  }

  function waterSection(compact) {
    var w = facts.water;
    var out = "<h2 id=\"water\">The water</h2>";
    var s = w.surf || {};
    var sd = s[dayKey];
    if (sd) {
      out += "<h3>Surf forecast, " + esc(s.county) + tag("Rule") + "</h3><dl class=\"kv\">" +
        "<dt>Surf</dt><dd>" + esc(sd.surf_text || "n/a") + (sd.swell_from_deg ? ", swell from " + esc(sd.swell_from_deg) + "°" : "") + "</dd>" +
        "<dt>Rip current risk</dt><dd>" + esc(sd.rip_risk || "n/a") + "</dd>" +
        (sd.water_temp_lo_f ? "<dt>Surf-zone water</dt><dd>" + esc(sd.water_temp_lo_f) + "-" + esc(sd.water_temp_hi_f) + "°F</dd>" : "") + "</dl>";
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
    out += thermocline();
    if (!compact) {
      out += "<h3>Satellite maps</h3><p class=\"small\">" +
        (w.sst_field ? "Sea-surface temperature map: " + esc(w.sst_field.date_label) + " (" + esc(w.sst_field.age_days) + " days old). " : "") +
        (w.chl ? "Chlorophyll (water color): " + esc(w.chl.date_label) + ", " + esc(w.chl.coverage_pct) + "% of the area seen through clouds. " : "") +
        '<a href="/">Open the map</a>.</p>';
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

  function thermocline() {
    var t = facts.water.thermocline || {};
    var o = t.observed;
    var out = "<h3>Thermocline</h3>";
    if (o) {
      out += '<p class="small">' + tag("Observed") + " Glider " + esc(o.glider) + ", " + esc(o.nm_from_sd) + " nm from San Diego (" +
        esc(o.lat.toFixed(2)) + "N " + esc(Math.abs(o.lng).toFixed(2)) + "W), " + esc(o.time) + ": " +
        esc(o.surface_f) + "°F at the surface" + (o.t30_f != null ? ", " + esc(o.t30_f) + "°F at 30 ft" : "") +
        (o.t60_f != null ? ", " + esc(o.t60_f) + "°F at 60 ft" : "") + (o.t100_f != null ? ", " + esc(o.t100_f) + "°F at 100 ft" : "") +
        "; sharpest drop near " + esc(o.thermocline_ft) + " ft (" + esc(o.drop_f_per_10ft) + "°F per 10 ft). " + link(o.url, "Glider data") + "</p>" +
        '<p class="note">' + esc(o.note) + "</p>";
    }
    if (t.spots && t.spots.length) {
      out += '<p class="small">' + tag("Modeled") + " Our heuristic at the kelp: " + t.spots.map(function (s) {
        return esc(s.name) + " " + (s.no_cliff ? "no thermocline" : "about " + esc(s.cliff_ft) + " ft");
      }).join("; ") + '.</p><p class="note">' + esc(t.method) + (o ? " Where it disagrees with the glider, trust the glider offshore and neither at the kelp." : "") + "</p>";
    }
    return out;
  }

  function diversCorner(spear) {
    var d = facts.divers;
    var out = "<h2 id=\"divers\">" + (spear ? "Hunting conditions" : "Divers' corner") + "</h2>";
    if (spear) out += '<p class="small">' + chip(d.hunting_vis.level, levelLabel(d.hunting_vis.level)) + " " + esc(d.hunting_vis.text) + "</p>";
    if (!spear && d.best_bet) out += '<p class="small"><b>' + esc(d.best_bet.text) + "</b></p>";
    out += "<h3>Visibility reports" + tag("Reported") + "</h3>";
    out += d.posts.length ? '<ul class="plain">' + d.posts.map(function (p) {
      return "<li><b>" + esc(p.vis) + "</b> at " + esc(p.site) + ", " + link(p.url, p.source) + ", " + esc(p.date_label) +
        (p.days_ago ? " (" + esc(p.days_ago) + (p.days_ago === 1 ? " day" : " days") + " ago)" : " (today)") + "</li>";
    }).join("") + "</ul>" : '<p class="small mut">No dive-shop visibility report in the last three days.</p>';
    if (d.stale_sources.length) out += '<p class="note">Not shown because their posts have not changed in days: ' + esc(d.stale_sources.join(", ")) + ".</p>";
    if (d.surge.sites.length) {
      out += "<h3>Surge at depth" + tag("Modeled") + '</h3><div class="scroll"><table><thead><tr><th>Site</th><th class="n">Depth</th><th class="n">Bottom water speed</th><th>Reading</th></tr></thead><tbody>' +
        d.surge.sites.map(function (s) {
          return "<tr><td>" + esc(s.site) + '</td><td class="n">' + esc(s.depth_ft) + ' ft</td><td class="n">' + esc(s.u_ft_s) + " ft/s</td><td>" +
            esc(s.level === "strong" ? "Strong: expect a stirred-up bottom" : s.level === "moderate" ? "Moderate: fine sand lifting" : "Light") + "</td></tr>";
        }).join("") + '</tbody></table></div><p class="note">' + esc(d.surge.method) + "</p>";
    }
    out += "<h3>Exposure suit" + tag(d.suit.tag) + "</h3><p class=\"small\">" + esc(d.suit.text) + "</p>";
    out += "<h3>Water quality</h3><p class=\"small\">";
    if (d.rain) out += "Rain in the last 72 hours at " + esc(d.rain.gauge) + ": " + esc(d.rain.inches.toFixed(2)) + " in" +
      (d.rain.advisory ? ". The county advises against ocean contact for 72 hours after rain." : ".") + tag("Observed") + " ";
    out += esc(d.water_quality.text) + " " + link(d.water_quality.url, "San Diego County beach status") + ".</p>";
    if (spear) out += "<h3>Current</h3><p class=\"small\">" + esc(facts.can_i_go.boat_dive.current) + "</p>";
    out += "<h3>In season" + tag("Rule") + '</h3><ul class="plain">' + d.season.map(function (s) {
      return "<li><b>" + esc(s.label) + ":</b> " + esc(s.text) + ' <span class="mut small">(' + link(s.source, "CDFW") + ", checked " + esc(s.verified_on) + ")</span></li>";
    }).join("") + "</ul>";
    if (spear) out += '<p class="note">No take of any kind inside state marine reserves, such as Matlahuayl (La Jolla Cove and Shores) and Cabrillo (the tip of Point Loma). ' +
      link("https://wildlife.ca.gov/Conservation/Marine/MPAs", "Check the CDFW MPA map") + " before you hunt a new spot.</p>";
    return out;
  }

  function inshoreTable(spearOnly) {
    var rows = (facts.fishing && facts.fishing.inshore) || [];
    if (spearOnly) rows = rows.filter(function (r) { return r.spear; });
    if (!rows.length) return "";
    return "<h2 id=\"inshore\">" + (spearOnly ? "What's around to hunt" : "Inshore and islands") + "</h2>" +
      '<ul class="verdicts">' + rows.map(function (r) {
        return "<li><details><summary><span class=\"name\">" + esc(r.label) + "</span><span class=\"tag\">" + esc(r.status) + "</span>" +
          '<span class="why">' + esc(r.today || r.drivers) + "</span></summary><div class=\"factors\">" +
          "<div><b>What drives it:</b> " + esc(r.drivers) + "</div><div><b>Where to look:</b> " + esc(r.look) + "</div>" +
          '<div class="rule">Evidence: ' + esc(r.evidence) + "</div></div></details></li>";
      }).join("") + "</ul>";
  }

  function cueWords(c) { return { "break": "break", bank: "bank", blue: "blue water", paddy: "paddies" }[c] || c; }

  function offshore() {
    var f = facts.fishing;
    if (!f) return "";
    var out = "<h2 id=\"offshore\">Offshore: water against each species</h2><p class=\"mut small\">" + esc(f.method) + "</p>";
    out += f.species.length ? '<ul class="verdicts">' + f.species.map(function (s) {
      var lv = s.status === "profile met" ? "within" : s.status === "water marginal" || s.status === "water right, season wrong" ? "marginal" : "outside";
      var best = s.best.map(function (b) {
        return esc(b.name) + " (" + esc(b.sst_f) + "°F" + (b.cues.length ? ", " + esc(b.cues.map(cueWords).join(", ")) : "") + ")";
      }).join("; ");
      return "<li><details><summary><span class=\"name\">" + esc(s.label) + "</span>" + chip(lv, s.status) +
        '<span class="why">' + esc(s.season) + ". " + esc(s.spots_in_band) + " of " + esc(s.spots_checked) + " spots in its " +
        esc(s.bite_band_f[0]) + "-" + esc(s.bite_band_f[1]) + "°F band" + (best ? ". Closest matches: " + best : "") + ".</span></summary>" +
        '<div class="factors"><div>' + esc(s.note) + "</div>" +
        (s.ambient.length ? '<div class="rule">Everywhere today, so not counted: ' + esc(s.ambient.map(cueWords).join(", ")) + ".</div>" : "") +
        "</div></details></li>";
    }).join("") + "</ul>" : '<p class="small mut">No satellite temperature map, so no species picture today.</p>';
    if (f.spots.length) {
      out += "<h3>Named spots" + tag("Observed") + '</h3><div class="scroll"><table><thead><tr><th>Spot</th><th class="n">nm</th><th class="n">Water</th><th>Nearest break</th><th class="n hide-sm">Chl</th><th class="hide-sm">Paddies</th></tr></thead><tbody>' +
        f.spots.slice().sort(function (a, b) { return a.nm_from_sd - b.nm_from_sd; }).map(function (s) {
          var b = s["break"];
          var pad = s.paddy == null ? "" : s.paddy <= 0.3 ? "core" : s.paddy <= 0.5 ? "likely" : "possible";
          return "<tr><td>" + esc(s.name) + '</td><td class="n">' + esc(s.nm_from_sd) + '</td><td class="n">' + esc(s.sst_f != null ? s.sst_f + "°F" : "") +
            "</td><td>" + (b ? esc([b.step_f + "°F over", b.over_nm, "nm,", b.direction ? [b.nm, "nm", b.direction].join(" ") : "here", ", warm", b.warm_side].join(" ").replace(" ,", ",")) : '<span class="mut">none within 10 nm</span>') +
            '</td><td class="n hide-sm">' + esc(s.chl != null ? s.chl : "") + '</td><td class="hide-sm">' + esc(pad) + "</td></tr>";
        }).join("") + '</tbody></table></div><p class="note">Water and breaks from the ' + esc((f.sst_field || {}).date_label || "latest") +
        " satellite map; chlorophyll in mg/m³ (lower is bluer); paddies from today's drift model. " + esc(f.counts_note) + "</p>";
    }
    return out;
  }

  function fleet() {
    var f = facts.fishing || {};
    var now = f.fleet_now, rec = f.fleet_recent;
    var out = "<h2 id=\"fleet\">Where the fleet is working</h2>";
    if (now && now.at_sea.length) {
      out += "<h3>Right now" + tag("Observed") + '</h3><ul class="plain">' + now.at_sea.map(function (b) {
        return "<li><b>" + esc(b.name) + "</b>: " + esc(b.stopped ? "stopped" : ["under way at", b.sog, "kt"].join(" ")) +
          (b.spot ? " near " + esc(b.spot) : "") + ", " + esc(b.nm_from_sd) + " nm from San Diego <span class=\"mut small\">(" + esc(localTime(b.t)) + ")</span></li>";
      }).join("") + '</ul><p class="note">Positions under ' + esc(now.max_age_h) + " hours old. " + link("/fleet/#view=live", "Live fleet map") + ".</p>";
    } else {
      out += '<p class="small mut">No boats heard at sea in the last few hours.</p>';
    }
    if (rec) {
      out += "<h3>Last three days" + tag("Observed") + "</h3>" + (rec.spots.length ? '<ul class="plain">' + rec.spots.map(function (s) {
        return "<li><b>" + esc(s.spot) + "</b>: " + esc(s.hours) + " h stopped, " + esc(s.boats.join(", ")) + " (" + esc(s.days.join(", ")) + ")</li>";
      }).join("") + "</ul>" : '<p class="small mut">No offshore stops recorded.</p>') +
        '<p class="note">A stop says the fleet worked there, not that fish were caught. ' + esc(rec.coverage_note) + "</p>";
    }
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

  function body() {
    if (view === "fishing") return canIGo(["boat", "shore"]) + offshore() + fleet() + inshoreTable(false) + waterSection(false);
    if (view === "spearfishing") return canIGo(["shore", "boat", "freedive"]) + diversCorner(true) + inshoreTable(true) + waterSection(true);
    return canIGo(["shore", "boatdive", "boat", "freedive"]) + diversCorner(false) + waterSection(true);
  }

  function render() {
    var lines = (facts.views && facts.views[view]) || facts.bottom_line;
    var tabs = '<div class="tabs" role="tablist" aria-label="Report view">' + VIEWS.map(function (v) {
      return '<button type="button" role="tab" aria-selected="' + (v[0] === view) + '" data-view="' + v[0] + '" class="' + (v[0] === view ? "on" : "") + '">' + v[1] + "</button>";
    }).join("") + "</div>";
    main.innerHTML =
      "<h1>Morning Report</h1><p class=\"mut\">" + esc(facts.date_label) + ". Built " + esc(facts.generated_local) + " from " +
      facts.sources.filter(function (s) { return s.status === "ok"; }).length + " of " + facts.sources.length + ' live inputs (<a href="#sources">list</a>).</p>' +
      tabs + '<div class="bottom">' + lines.map(function (l) { return "<p>" + esc(l) + "</p>"; }).join("") + "</div>" + body() + sourcesBlock();
    Array.prototype.forEach.call(main.querySelectorAll(".days button"), function (b) {
      b.addEventListener("click", function () { dayKey = b.getAttribute("data-day"); rerender(); });
    });
    Array.prototype.forEach.call(main.querySelectorAll(".tabs button"), function (b) {
      b.addEventListener("click", function () {
        view = b.getAttribute("data-view");
        try { localStorage.setItem("sd:report:view", view); } catch (e) { /* storage blocked */ }
        history.replaceState(null, "", "#view=" + view);
        rerender();
      });
    });
  }
  function rerender() { var y = window.scrollY; render(); window.scrollTo(0, y); }

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
