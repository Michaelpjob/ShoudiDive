'use strict';
/* Track-my-paddy UI. Engine lives in track.js (PT); this file is the
 * panel, the map rendering, and the day/time lookup.
 *
 * Presentation rule that drives every choice here: a drift forecast is a
 * SEARCH AREA, not a pin. The measured spread between our own current
 * products puts the 68% radius near 10 km at day 1 and 27 km at day 7,
 * so the coordinate is never shown without its radius, the map draws the
 * area rather than only a line, and the wording downgrades itself as the
 * area outgrows what a boat can actually search.
 */

var PTUI = (function () {
  var map, layer, F = null, FC = null, startLL = null, busy = false;
  var TIER_COLOR = { search: '#22c55e', wide: '#eab308', region: '#f97316' };

  function el(id) { return document.getElementById(id); }
  function ddm(v, pos, neg) {
    var h = v >= 0 ? pos : neg; v = Math.abs(v);
    var d = Math.floor(v);
    return d + '°' + ((v - d) * 60).toFixed(3) + "' " + h;
  }
  function fmtLL(lat, lng) {
    return { dm: ddm(lat, 'N', 'S') + '  ' + ddm(lng, 'E', 'W'),
             dd: lat.toFixed(4) + ', ' + lng.toFixed(4) };
  }
  function parseLL(s) { return PT.parseLatLng(s); }

  function hoursLabel(t) {
    var d = Math.floor(t / 24), h = Math.round(t % 24);
    return d === 0 ? ('+' + h + ' h') : ('Day ' + d + (h ? ' +' + h + ' h' : ''));
  }
  function clockLabel(t) {
    var when = new Date(Date.now() + t * 3600e3);
    return when.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric',
                                     hour: 'numeric', minute: '2-digit' });
  }

  function panelHTML() {
    return '' +
      '<div class="tk-head"><b>Track a paddy</b><span class="tk-beta">BETA</span>' +
        '<button class="tk-x" id="tkClose" title="Close">×</button></div>' +
      '<div class="tk-body">' +
        '<label class="tk-lab">Paddy position (where it is now)</label>' +
        '<div class="tk-row">' +
          '<input id="tkLL" class="tk-in" placeholder="33.4667, -119.3987" autocomplete="off"/>' +
          '<button id="tkPick" class="tk-btn tk-ghost" title="Then tap the map">Tap map</button>' +
        '</div>' +
        '<button id="tkRun" class="tk-btn tk-go">Forecast 7-day drift</button>' +
        '<div id="tkMsg" class="tk-msg"></div>' +
        '<div id="tkOut" class="tk-out" hidden>' +
          '<div class="tk-pick">' +
            '<label>Day <select id="tkDay"></select></label>' +
            '<label>Time <select id="tkHour"></select></label>' +
          '</div>' +
          '<input type="range" id="tkScrub" class="tk-scrub" min="0" max="168" step="1" value="24"/>' +
          '<div id="tkWhen" class="tk-when"></div>' +
          '<div id="tkTier" class="tk-tier"></div>' +
          '<div class="tk-coord"><code id="tkDM"></code><code id="tkDD"></code>' +
            '<button id="tkCopy" class="tk-btn tk-ghost">Copy</button></div>' +
          '<div class="tk-legend">' +
            '<span><i class="tk-sw tk-sw-lane"></i>7-day lane</span>' +
            '<span><i class="tk-sw tk-sw-sel"></i>likely now</span>' +
            '<span><i class="tk-sw tk-sw-dot"></i>model runs</span>' +
          '</div>' +
          '<div id="tkNote" class="tk-note"></div>' +
          '<div id="tkSrc" class="tk-src"></div>' +
        '</div>' +
      '</div>';
  }

  function setMsg(s, bad) {
    var m = el('tkMsg'); if (!m) return;
    m.textContent = s || ''; m.className = 'tk-msg' + (bad ? ' bad' : '');
  }

  // Corridor geometry lives in track.js (PT) so it can be unit-tested
  // without a DOM. Two width rules, deliberately different:
  //   growingWidth - the 7-day lane, which widens as the ensemble spreads
  //   fixedWidth   - the selected-hour band, one width for the whole
  //                  stretch, because the spread AT that hour is one number
  function growingWidth(steps) {
    return function (i) { return steps[i].crossKm; };
  }
  function fixedWidth(km) {
    return function () { return km; };
  }

  function draw(step) {
    if (!layer) return;
    layer.clearLayers();
    if (!FC) return;
    var steps = FC.steps;

    // Lane, ONLY for as long as one exists. Past FC.laneEndH the ensemble
    // has fanned out and a ribbon would be inventing structure — and, drawn
    // geometrically, would happily paint across San Diego.
    var laneEnd = 0;
    for (var q = 0; q < steps.length; q++) if (steps[q].t <= FC.laneEndH) laneEnd = q;
    if (laneEnd > 1) {
      L.polygon(PT.corridor(steps, 0, laneEnd, growingWidth(steps)),
        { color: '#38bdf8', weight: 1, opacity: 0.35, fillColor: '#38bdf8', fillOpacity: 0.06,
          dashArray: '4 4' }).addTo(layer);
    }

    // The drifted path, coloured by how tight the lane still is.
    for (var i = 1; i < steps.length; i++) {
      L.polyline([[steps[i - 1].lat, steps[i - 1].lng], [steps[i].lat, steps[i].lng]],
        { color: TIER_COLOR[steps[i].tier.key], weight: 3, opacity: 0.9 }).addTo(layer);
    }
    // Day ticks along the lane.
    steps.forEach(function (s) {
      if (s.t === 0 || s.t % 24 !== 0) return;
      L.circleMarker([s.lat, s.lng], { radius: 3, color: '#0b1220', weight: 1,
        fillColor: TIER_COLOR[s.tier.key], fillOpacity: 1 })
        .bindTooltip('Day ' + (s.t / 24) + ' · ±' + Math.round(s.crossKm) + ' km either side',
          { direction: 'top' }).addTo(layer);
    });
    if (startLL) {
      L.circleMarker([startLL.lat, startLL.lng], { radius: 6, color: '#0b1220', weight: 2,
        fillColor: '#38bdf8', fillOpacity: 1 }).bindTooltip('Paddy seen here').addTo(layer);
    }
    if (step) {
      // Where it plausibly is AT THIS TIME: the stretch of lane within the
      // along-track error, highlighted — not a circle around a fake pin.
      // `si`, not `i` - the polyline loop above already declares a
      // function-scoped `var i`, and re-declaring it here reassigned the
      // same binding. Harmless as written, but exactly the shadowing that
      // goes wrong the moment either block moves.
      var si = steps.indexOf(step);
      if (si < 0) si = 0;
      // Where the ensemble ACTUALLY is at this time — one dot per member.
      // This replaces the drawn amber blob: the dots cannot stray onto
      // land the model never sent them to, and their density shows the
      // real shape of the answer instead of a smooth invented one.
      (step.cloud || []).forEach(function (c) {
        L.circleMarker([c[1], c[0]], { radius: 2.2, stroke: false,
          fillColor: '#fbbf24', fillOpacity: 0.5, interactive: false }).addTo(layer);
      });
      if (step.t <= FC.laneEndH) {
        // ONE width for the whole stretch — the cross-track spread at the
        // selected hour. Per-step widths here drew a lopsided wedge that
        // ramped from well under to well over the +-km the panel quotes.
        var sp = PT.alongSpan(steps, si);
        L.polygon(PT.corridor(steps, sp[0], sp[1], fixedWidth(step.crossKm)),
          { color: '#fbbf24', weight: 1.5, opacity: 0.85, fillColor: '#fbbf24', fillOpacity: 0.09 })
          .bindTooltip('Likely stretch of lane at the selected time', { sticky: true }).addTo(layer);
      }
      L.circleMarker([step.lat, step.lng], { radius: 6, color: '#0b1220', weight: 2,
        fillColor: '#fbbf24', fillOpacity: 1 })
        .bindTooltip('Centre of the ensemble', { direction: 'top' }).addTo(layer);
    }
    var pts = steps.map(function (s) { return [s.lat, s.lng]; });
    if (pts.length > 1) map.fitBounds(L.latLngBounds(pts).pad(0.3));
  }

  function stepAt(t) {
    if (!FC || !FC.steps.length) return null;
    var best = FC.steps[0], bd = 1e9;
    FC.steps.forEach(function (s) { var d = Math.abs(s.t - t); if (d < bd) { bd = d; best = s; } });
    return best;
  }

  function show(t) {
    var s = stepAt(t); if (!s) return;
    var f = fmtLL(s.lat, s.lng);
    el('tkWhen').textContent = hoursLabel(s.t) + '  ·  ' + clockLabel(s.t);
    var tier = s.tier;
    el('tkTier').innerHTML = '<span class="tk-dot" style="background:' + TIER_COLOR[tier.key] + '"></span>' +
      '<b>' + tier.label + '</b> · ±' + Math.round(s.crossKm) + ' km either side of the line' +
      ' <span class="tk-sub">(±' + Math.round(s.alongKm) + ' km along it)</span>';
    el('tkDM').textContent = f.dm;
    el('tkDD').textContent = f.dd;
    // "still being tracked", NOT "still floating" — sinking is not modelled.
    var inDomainPct = Math.round((s.inDomain != null ? s.inDomain : 1) * 100);
    el('tkNote').textContent =
      (s.t > FC.laneEndH
        ? 'No usable lane this far out — the forecast has fanned out, so the dots are the honest answer. '
        : tier.note + ' ') +
      (inDomainPct < 95 ? inDomainPct + '% of runs still in the forecast area here. ' : '') +
      (FC.truncated && s.t >= FC.hoursCovered - 1
        ? 'Track ends here: the paddy left the forecast area.' : '');
    el('tkScrub').value = s.t;
    el('tkDay').value = String(Math.floor(s.t / 24));
    // The Time control is the CLOCK time of day the user would be on the
    // water, not hours-into-the-forecast — those differ by whatever time
    // it is right now, and showing the offset read as a wrong clock.
    el('tkHour').value = String(new Date(t0Of() + s.t * 3600e3).getHours());
    draw(s);
  }

  function t0Of() { return (FC && FC.t0) || Date.now(); }

  // (day offset, clock hour) -> hours from now. Within day `d` the clock
  // hour lands at d*24 + ((H - hour_now + 24) % 24).
  function offsetFor(d, H) {
    var startHour = new Date(t0Of()).getHours();
    return d * 24 + ((H - startHour + 24) % 24);
  }

  function syncFromPickers() {
    var d = parseInt(el('tkDay').value, 10) || 0;
    var h = parseInt(el('tkHour').value, 10) || 0;
    var t = offsetFor(d, h);
    show(Math.max(0, Math.min(FC ? FC.hoursCovered : 168, t)));
  }

  function run() {
    if (busy) return;
    var ll = parseLL(el('tkLL').value);
    if (!ll) {
      setMsg('Could not read that. Try 32 56 0000 117 52 000, ' +
             '32 56.000 117 52.000, or 32.9333 -117.8667', true);
      return;
    }
    // Echo the reading back — several plotter formats look alike, and a
    // misparse would silently forecast the wrong piece of ocean.
    var echo = fmtLL(ll.lat, ll.lng);
    startLL = ll; busy = true;
    setMsg('Loading forecast currents and wind…');
    var t0 = Date.now();
    var job = F ? Promise.resolve(F) : PT.loadForcing(t0);
    job.then(function (forcing) {
      F = forcing;
      if (!F.rtofs.length && !F.surface.length) {
        setMsg('No forecast current data available right now.', true); busy = false; return;
      }
      setMsg('Running drift ensemble…');
      // Yield so the message paints before the (synchronous) ensemble.
      setTimeout(function () {
        try {
          FC = PT.forecast(F, ll.lng, ll.lat, 168);
          FC.t0 = t0;
        } catch (e) { setMsg('Drift failed: ' + e.message, true); busy = false; return; }
        busy = false;
        if (!FC.steps || FC.steps.length < 2) {
          setMsg('That position has no current data — is it on land or outside the map?', true);
          return;
        }
        var days = Math.floor(FC.hoursCovered / 24);
        var src = F.sources || {};
        el('tkSrc').textContent = 'Forcing: ' + (src.rtofs || 0) + ' ocean-model + ' +
          (src.surface || 0) + ' surface-current + ' + (src.wind || 0) + ' wind fields' +
          (F.notes && F.notes.length ? ' — ' + F.notes.join('; ') : '');
        setMsg('Read as ' + echo.dm + ' (' + ll.how + '). ' + (FC.truncated
          ? 'Tracked ' + days + ' day(s) — it drifts out of the forecast area after that.'
          : '7-day drift, ' + PT.consts.N_MEMBERS + '-member ensemble.'));
        el('tkOut').hidden = false;
        var dsel = el('tkDay'); dsel.innerHTML = '';
        for (var d = 0; d <= days; d++) {
          var o = document.createElement('option'); o.value = d;
          o.textContent = d === 0 ? 'Today' : 'Day ' + d; dsel.appendChild(o);
        }
        var hsel = el('tkHour'); hsel.innerHTML = '';
        for (var h = 0; h < 24; h++) {
          var oh = document.createElement('option'); oh.value = h;
          oh.textContent = (h < 10 ? '0' : '') + h + ':00'; hsel.appendChild(oh);
        }
        el('tkScrub').max = FC.hoursCovered;
        show(Math.min(24, FC.hoursCovered));
      }, 30);
    }).catch(function (e) {
      setMsg('Could not load forecast data: ' + e.message, true); busy = false;
    });
  }

  function init(m) {
    map = m;
    layer = L.layerGroup().addTo(map);
    var wrap = document.createElement('div');
    wrap.className = 'tkpanel'; wrap.id = 'tkpanel'; wrap.hidden = true;
    wrap.innerHTML = panelHTML();
    document.body.appendChild(wrap);

    var btn = document.createElement('button');
    btn.id = 'tkOpen'; btn.className = 'logbtn';
    btn.title = 'Forecast where a paddy you found will drift';
    btn.textContent = '◎ Track a paddy';
    var bar = document.querySelector('.bar');
    if (bar) bar.appendChild(btn);

    btn.onclick = function () { wrap.hidden = !wrap.hidden; };
    el('tkClose').onclick = function () { wrap.hidden = true; };
    el('tkRun').onclick = run;
    el('tkLL').addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); });
    el('tkScrub').addEventListener('input', function () { show(parseInt(this.value, 10)); });
    el('tkDay').addEventListener('change', syncFromPickers);
    el('tkHour').addEventListener('change', syncFromPickers);
    el('tkCopy').onclick = function () {
      var s = stepAt(parseInt(el('tkScrub').value, 10)); if (!s) return;
      var f = fmtLL(s.lat, s.lng);
      var txt = 'Paddy drift forecast — ' + hoursLabel(s.t) + ' (' + clockLabel(s.t) + ')\n' +
        f.dm + '  (' + f.dd + ')\n' +
        'Corridor ±' + Math.round(s.crossKm) + ' km either side, ±' +
        Math.round(s.alongKm) + ' km along the line — ' + s.tier.label + '. ' +
        s.tier.note + '\nModelled drift, not an observation.';
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () {
        el('tkCopy').textContent = '✓'; setTimeout(function () { el('tkCopy').textContent = 'Copy'; }, 1500);
      });
    };
    var picking = false;
    el('tkPick').onclick = function () { picking = true; setMsg('Tap the paddy position on the map…'); };
    map.on('click', function (e) {
      if (!picking) return;
      picking = false;
      el('tkLL').value = e.latlng.lat.toFixed(4) + ', ' + e.latlng.lng.toFixed(4);
      setMsg('');
    });
  }

  return { init: init, parseLL: parseLL, hoursLabel: hoursLabel };
})();
