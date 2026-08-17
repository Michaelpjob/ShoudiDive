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
  // Accepts "33.4667, -119.3987", "33.4667 -119.3987", or two fields.
  function parseLL(s) {
    if (!s) return null;
    var m = String(s).replace(/[^\d.\-+, ]/g, ' ').match(/(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)/);
    if (!m) return null;
    var a = parseFloat(m[1]), b = parseFloat(m[2]);
    if (!isFinite(a) || !isFinite(b)) return null;
    // SoCal: lat ~31-42 N, lng ~-128..-116. Accept either order.
    if (Math.abs(a) <= 90 && Math.abs(b) > 90) return { lat: a, lng: b };
    if (Math.abs(b) <= 90 && Math.abs(a) > 90) return { lat: b, lng: a };
    return { lat: a, lng: b };
  }
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
          '<div id="tkNote" class="tk-note"></div>' +
        '</div>' +
      '</div>';
  }

  function setMsg(s, bad) {
    var m = el('tkMsg'); if (!m) return;
    m.textContent = s || ''; m.className = 'tk-msg' + (bad ? ' bad' : '');
  }

  // Offset a point by km in the km-space direction (dx east, dy north).
  function offKm(lat, lng, dx, dy, km) {
    var kLat = 111.132, kLng = 111.320 * Math.cos(lat * Math.PI / 180);
    return [lat + (dy * km) / kLat, lng + (dx * km) / Math.max(1e-6, kLng)];
  }
  // Unit along-track direction at step i, in km-space. Uses a WIDE stencil
  // (+-6 h): a 1-hour stencil follows every wiggle in the hourly track, and
  // when the direction flips the left/right offsets cross over and the
  // ribbon renders as spikes. Smoothing the heading keeps the lane clean.
  var DIR_STENCIL = 6;
  function dirAt(steps, i) {
    var a = steps[Math.max(0, i - DIR_STENCIL)],
        b = steps[Math.min(steps.length - 1, i + DIR_STENCIL)];
    var kLat = 111.132, kLng = 111.320 * Math.cos(steps[i].lat * Math.PI / 180);
    var dx = (b.lng - a.lng) * kLng, dy = (b.lat - a.lat) * kLat;
    var L = Math.sqrt(dx * dx + dy * dy);
    return L < 1e-6 ? [1, 0] : [dx / L, dy / L];
  }
  // The corridor: centre track offset +-crossKm, as one closed ribbon.
  // Vertices are DECIMATED to every 6th hour. At full hourly density the
  // offset points on the inside of every small bend overlap each other and
  // the polygon self-intersects, which Leaflet renders as long spikes.
  // Sampling the lane every 6 h keeps the ribbon simple and convex enough
  // to draw cleanly, and 6 h of drift is far finer than the lane is wide.
  var RIBBON_STEP = 6;
  function corridor(steps, from, to) {
    var left = [], right = [], idx = [];
    for (var i = from; i <= to; i += RIBBON_STEP) idx.push(i);
    if (idx[idx.length - 1] !== to) idx.push(to);
    for (var k = 0; k < idx.length; k++) {
      var i = idx[k], d = dirAt(steps, i), s = steps[i];
      var px = -d[1], py = d[0];                 // left-hand perpendicular
      left.push(offKm(s.lat, s.lng, px, py, s.crossKm));
      right.push(offKm(s.lat, s.lng, -px, -py, s.crossKm));
    }
    return left.concat(right.reverse());
  }
  // Indices whose along-track distance from `i` is within +-alongKm.
  function alongSpan(steps, i) {
    var reach = steps[i].alongKm, lo = i, hi = i;
    while (lo > 0 && PT.haversineKm(steps[i].lat, steps[i].lng, steps[lo - 1].lat, steps[lo - 1].lng) < reach) lo--;
    while (hi < steps.length - 1 && PT.haversineKm(steps[i].lat, steps[i].lng, steps[hi + 1].lat, steps[hi + 1].lng) < reach) hi++;
    return [lo, hi];
  }

  function draw(step) {
    if (!layer) return;
    layer.clearLayers();
    if (!FC) return;
    var steps = FC.steps;

    // Full-week corridor: a narrow ribbon, not a disc. Cross-track error is
    // ~4x smaller than along-track, so the honest shape is a lane you run.
    L.polygon(corridor(steps, 0, steps.length - 1),
      { color: '#38bdf8', weight: 1, opacity: 0.45, fillColor: '#38bdf8', fillOpacity: 0.07 }).addTo(layer);

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
      var i = steps.indexOf(step);
      if (i < 0) i = 0;
      var sp = alongSpan(steps, i);
      L.polygon(corridor(steps, sp[0], sp[1]),
        { color: '#dc2626', weight: 2, opacity: 0.95, fillColor: '#dc2626', fillOpacity: 0.18 }).addTo(layer);
      L.circleMarker([step.lat, step.lng], { radius: 6, color: '#fff', weight: 2,
        fillColor: '#dc2626', fillOpacity: 1 }).addTo(layer);
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
    el('tkNote').textContent = tier.note +
      (FC.truncated && s.t >= FC.hoursCovered - 1
        ? ' Track ends here: the paddy left the forecast area.' : '');
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
    if (!ll) { setMsg('Enter a position like 33.4667, -119.3987', true); return; }
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
        setMsg(FC.truncated
          ? 'Tracked ' + days + ' day(s) — the paddy drifts out of the forecast area after that.'
          : '7-day drift from ' + PT.consts.N_MEMBERS + '-member ensemble.');
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
