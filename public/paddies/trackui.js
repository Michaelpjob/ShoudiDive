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
 *
 * Workflow is a three-state machine, and every state has an obvious way
 * out (2026-10 rework; the old panel had a run button and nothing else,
 * so a finished forecast could only be hidden, never cleared):
 *
 *   closed --open--> enter --run--> result --"Track another"--> enter
 *                      ^              |  \--"Edit position"----^
 *                      +--- × / Done / Esc / toolbar button: clears the
 *                           map and returns to closed, from any state
 *
 * In `enter` the map is a position picker: a tap places a draggable pin
 * (and does NOT also drop the page's GPS waypoint), typing echoes the
 * parse live, and "My GPS" uses the phone's fix — on a boat sitting
 * next to the paddy, that is the paddy's position.
 */

var PTUI = (function () {
  var map, layer, F = null, FC = null, startLL = null, busy = false;
  // SITE: the paddy hindcast bundle (data.json) handed in by app.js —
  // bed positions, shedding timeline, water temp. ORIGIN: the origin/age
  // estimate for the current run, or null when none could be made.
  var SITE = null, ORIGIN = null;
  var lastVia = 'typed';           // how the position was entered: typed | tap | gps
  var mode = 'closed';             // closed | enter | result
  var pin = null;                  // draggable pending-position marker (enter mode)
  var pending = null;              // parsed position waiting to be run
  var curStep = null;              // the step the result view is showing
  var wrap, openBtn;
  var TIER_COLOR = { search: '#22c55e', wide: '#eab308', region: '#f97316' };
  var PHONE = '(max-width:760px)';

  function el(id) { return document.getElementById(id); }
  function isPhone() { return !!(window.matchMedia && window.matchMedia(PHONE).matches); }
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
    return d === 0 ? ('+' + h + ' h') : ('+' + d + ' d' + (h ? ' ' + h + ' h' : ''));
  }
  function clockLabel(t) {
    var when = new Date(t0Of() + t * 3600e3);
    return when.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric',
                                     hour: 'numeric', minute: '2-digit' });
  }
  function shortClock(t) {
    var when = new Date(t0Of() + t * 3600e3);
    return when.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  }

  function panelHTML() {
    return '' +
      '<div class="tk-head" id="tkHead"><b>Track a paddy</b><span class="tk-beta">BETA</span>' +
        '<span class="tk-mini" id="tkMini"></span>' +
        '<button class="tk-hbtn tk-min" id="tkMin" title="Minimize" aria-label="Minimize">▾</button>' +
        '<button class="tk-hbtn tk-x" id="tkClose" title="Close and clear (Esc)" aria-label="Close">×</button></div>' +
      '<div class="tk-body">' +
        // ---- step 1: where is it ------------------------------------
        '<div id="tkEnter">' +
          '<div class="tk-q">Where is the paddy now?</div>' +
          '<div class="tk-hint">Tap it on the map, use your GPS, or type the plotter position.</div>' +
          '<div class="tk-row">' +
            '<input id="tkLL" class="tk-in" placeholder="32 56.000 117 52.000" autocomplete="off" ' +
              'inputmode="text" spellcheck="false"/>' +
            '<button id="tkGps" class="tk-btn tk-ghost" title="Use this device\'s GPS fix">My GPS</button>' +
          '</div>' +
          '<div id="tkEcho" class="tk-echo"></div>' +
          '<button id="tkRun" class="tk-btn tk-go" disabled>Forecast drift</button>' +
          '<div class="tk-disclose">Runs are logged anonymously (position + format) to improve the model. ' +
            'Honors Do&nbsp;Not&nbsp;Track.</div>' +
        '</div>' +
        // ---- step 2: the forecast -----------------------------------
        '<div id="tkResult" hidden>' +
          '<div class="tk-from">Seen at <code id="tkFrom"></code>' +
            '<button id="tkEdit" class="tk-link">Edit</button></div>' +
          '<div id="tkSum" class="tk-sum"></div>' +
          '<div class="tk-pick">' +
            '<label>Day <select id="tkDay"></select></label>' +
            '<label>Time <select id="tkHour"></select></label>' +
          '</div>' +
          '<input type="range" id="tkScrub" class="tk-scrub" min="0" max="168" step="1" value="24" ' +
            'aria-label="Forecast time"/>' +
          '<div id="tkWhen" class="tk-when"></div>' +
          '<div id="tkTier" class="tk-tier"></div>' +
          '<div class="tk-coord"><code id="tkDM"></code><code id="tkDD"></code>' +
            '<button id="tkCopy" class="tk-btn tk-ghost">Copy</button></div>' +
          '<div id="tkNote" class="tk-note"></div>' +
          '<details id="tkMore" class="tk-more"><summary>Map key, origin &amp; model details</summary>' +
            '<div class="tk-legend">' +
              '<span><i class="tk-sw tk-sw-lane"></i>search lane</span>' +
              '<span><i class="tk-sw tk-sw-sel"></i>likely at this time</span>' +
              '<span><i class="tk-sw tk-sw-dot"></i>model runs</span>' +
              '<span><i class="tk-sw tk-sw-beach"></i>beached</span>' +
              '<span><i class="tk-sw tk-sw-dash"></i>direction only</span>' +
            '</div>' +
            '<div id="tkOrigin" class="tk-origin" hidden></div>' +
            '<div id="tkSrc" class="tk-src"></div>' +
          '</details>' +
          '<div class="tk-actions">' +
            '<button id="tkAnother" class="tk-btn tk-go">+ Track another paddy</button>' +
            '<button id="tkDone" class="tk-btn tk-ghost">Done</button>' +
          '</div>' +
        '</div>' +
        '<div id="tkMsg" class="tk-msg"></div>' +
      '</div>';
  }

  /* Anonymous run logging. One event per forecast run carrying the
     ENTERED position and how it was given (typed format vs map tap) —
     the product question is where people actually find paddies vs
     where the model says they should be. Mirrors the main site's
     analytics contract exactly: same endpoint, same payload shape,
     same sessionStorage session id (dies with the tab), no IP or UA
     server-side, and the same two opt-outs (browser DNT and
     localStorage sd:analytics:off). Disclosed in the panel. */
  function logRun(ll, via) {
    try {
      if (navigator.doNotTrack === '1' || window.doNotTrack === '1') return;
      if (localStorage.getItem('sd:analytics:off') === '1') return;
      var sid = sessionStorage.getItem('sd:sid');
      if (!sid) {
        var a = new Uint32Array(2);
        (window.crypto || {}).getRandomValues ? crypto.getRandomValues(a) : (a = [Date.now(), 0]);
        sid = a[0].toString(16) + a[1].toString(16);
        sessionStorage.setItem('sd:sid', sid);
      }
      var payload = JSON.stringify({
        session_id: sid,
        viewport: window.innerWidth < 600 ? 'mobile' : (window.innerWidth < 1024 ? 'tablet' : 'desktop'),
        sent_at: new Date().toISOString().slice(0, 16) + 'Z',
        events: [{
          name: 'paddy_track_run',
          ts: new Date().toISOString().slice(0, 16) + 'Z',
          props: { lat: Math.round(ll.lat * 1e4) / 1e4, lng: Math.round(ll.lng * 1e4) / 1e4,
                   fmt: String(ll.how || ''), via: via }
        }]
      });
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/analytics/event', new Blob([payload], { type: 'application/json' }));
      } else {
        fetch('/api/analytics/event', { method: 'POST', body: payload,
          headers: { 'Content-Type': 'application/json' }, keepalive: true }).catch(function () {});
      }
    } catch (e) { /* logging must never break a run */ }
  }

  function setMsg(s, bad) {
    var m = el('tkMsg'); if (!m) return;
    m.textContent = s || ''; m.className = 'tk-msg' + (bad ? ' bad' : '');
  }

  /* ---- map rendering ------------------------------------------------
     "Within N km of this stretch of track" is drawn as a fat, round-capped
     STROKE along the centre track, not as a polygon offset sideways from
     it. The offset polygon (PT.corridor) is exact on a straight run, but
     paddies in slow, turning water (the common case near the coast:
     measured 5 km of drift against +-8 km of spread on day 1) get a lane
     WIDER than it is long, and the perpendicular offsets at each end
     fanned out into slivers and lopsided pentagons that pointed nowhere
     the paddy goes. A buffered stroke is the same "+-N km either side"
     statement, follows every bend, and degrades to a plain disc when the
     paddy barely moves — which is the honest picture of a paddy that
     barely moves.

     Each shape family sits in its own pane with GROUP opacity, so the
     overlapping round caps of consecutive segments composite once
     instead of darkening every joint. */
  var laneR = null, selR = null;
  function makePane(name, z, op) {
    var p = map.getPane(name) || map.createPane(name);
    p.style.zIndex = z; p.style.opacity = op; p.style.pointerEvents = 'none';
    return L.svg({ pane: name });
  }
  // km -> screen px at a latitude, for the current zoom.
  function kmPx(lat, lng, km) {
    var a = map.latLngToContainerPoint([lat, lng]);
    var b = map.latLngToContainerPoint([lat + km / 110.574, lng]);
    return Math.abs(a.y - b.y);
  }
  function buffer(pts, halfKm, color, pane, renderer) {
    var lat = pts[0][0], lng = pts[0][1];
    return L.polyline(pts, { pane: pane, renderer: renderer, color: color, opacity: 1,
      weight: Math.max(6, 2 * kmPx(lat, lng, halfKm)), lineCap: 'round', lineJoin: 'round',
      interactive: false });
  }

  function draw(step) {
    if (!layer) return;
    layer.clearLayers();
    if (!FC) return;
    var steps = FC.steps;
    var si = step ? steps.indexOf(step) : 0;
    if (si < 0) si = 0;

    // Search lane, ONLY for as long as one exists. Past FC.laneEndH the
    // ensemble has fanned out and a lane would be inventing structure.
    // Widens with time: one segment per 3 h, each as wide as the spread
    // at its far end (the spread is a non-decreasing envelope).
    var laneEnd = 0;
    for (var q = 0; q < steps.length; q++) if (steps[q].t <= FC.laneEndH) laneEnd = q;
    if (laneEnd > 1) {
      var idx = [];
      for (q = 0; q <= laneEnd; q += 3) idx.push(q);
      if (idx[idx.length - 1] !== laneEnd) idx.push(laneEnd);
      for (var k = 1; k < idx.length; k++) {
        var a = steps[idx[k - 1]], b = steps[idx[k]];
        buffer([[a.lat, a.lng], [b.lat, b.lng]], b.crossKm, '#38bdf8', 'tkLane', laneR).addTo(layer);
      }
    }

    // Where it plausibly is AT THE SELECTED TIME: the stretch of track
    // within the along-track error, buffered by the cross-track spread at
    // that hour — one width for the whole stretch, because the spread AT
    // that hour is one number (the +-km the panel quotes).
    if (step && step.t > 0 && step.t <= FC.laneEndH) {
      var sp = PT.alongSpan(steps, si), seg = [];
      for (var j = sp[0]; j <= sp[1]; j++) seg.push([steps[j].lat, steps[j].lng]);
      if (seg.length === 1) seg.push(seg[0]);
      buffer(seg, step.crossKm, '#fbbf24', 'tkSel', selR).addTo(layer);
    }

    // The centre track, in runs of one style: bright up to the selected
    // time (where it has been), faint after (where it is going), and
    // dotted past the lane (direction only, no searchable width).
    var run = null;
    function flush() {
      if (!run) return;
      L.polyline(run.pts, { color: TIER_COLOR[run.tier], weight: run.ahead ? 2 : 3.5,
        opacity: run.ahead ? 0.45 : 0.95, dashArray: run.lane ? null : '2 7',
        lineCap: 'round', interactive: false }).addTo(layer);
    }
    for (var i = 1; i < steps.length; i++) {
      var s = steps[i], ahead = i > si, lane = s.t <= FC.laneEndH;
      var key = s.tier.key + (ahead ? 'a' : 'p') + (lane ? 'l' : 'd');
      if (!run || run.key !== key) {
        flush();
        run = { key: key, tier: s.tier.key, ahead: ahead, lane: lane,
                pts: [[steps[i - 1].lat, steps[i - 1].lng]] };
      }
      run.pts.push([s.lat, s.lng]);
    }
    flush();

    // Day ticks along the track.
    steps.forEach(function (s) {
      if (s.t === 0 || s.t % 24 !== 0) return;
      L.circleMarker([s.lat, s.lng], { radius: 3, color: '#0b1220', weight: 1,
        fillColor: TIER_COLOR[s.tier.key], fillOpacity: 1 })
        .bindTooltip(shortClock(s.t) + ' · ±' + Math.round(s.crossKm) + ' km either side',
          { direction: 'top' }).addTo(layer);
    });
    if (startLL) {
      L.circleMarker([startLL.lat, startLL.lng], { radius: 6, color: '#0b1220', weight: 2,
        fillColor: '#38bdf8', fillOpacity: 1 }).bindTooltip('Paddy seen here').addTo(layer);
    }
    if (step) {
      // Where the ensemble ACTUALLY is at this time — one dot per run.
      // The dots cannot stray onto land the model never sent them to,
      // and their density shows the real shape of the answer.
      (step.cloud || []).forEach(function (c) {
        L.circleMarker([c[1], c[0]], { radius: 2.2, stroke: false,
          fillColor: '#fbbf24', fillOpacity: 0.5, interactive: false }).addTo(layer);
      });
      // Runs that BEACHED by this hour, frozen where they hit shore. Grey,
      // not amber: they are no longer floating and never move again.
      (step.beached || []).forEach(function (c) {
        L.circleMarker([c[1], c[0]], { radius: 2.4, color: '#0b1220', weight: 0.5,
          fillColor: '#94a3b8', fillOpacity: 0.85, interactive: false }).addTo(layer);
      });
      if (step.t > 0) {
        L.circleMarker([step.lat, step.lng], { radius: 7, color: '#0b1220', weight: 2,
          fillColor: '#fbbf24', fillOpacity: 1 })
          .bindTooltip('Most likely here at ' + shortClock(step.t), { direction: 'top' }).addTo(layer);
      }
    }
  }

  // Frame the forecast ONCE, after a run, leaving room for whatever panels
  // cover the map. Scrubbing never re-fits: the old code called fitBounds
  // on every slider tick, so the map lurched under the user's finger.
  function fitResult(step) {
    var pts = FC.steps.map(function (s) { return [s.lat, s.lng]; });
    (step && step.cloud || []).forEach(function (c) { pts.push([c[1], c[0]]); });
    var opt = { maxZoom: 11 };
    if (isPhone()) {
      var bar = document.querySelector('.bar');
      opt.paddingTopLeft = [16, (bar ? bar.offsetHeight : 60) + 22];
      opt.paddingBottomRight = [16, wrap.offsetHeight + 22];
    } else {
      var pn = el('panel');
      opt.paddingTopLeft = [wrap.offsetWidth + 34, 70];
      opt.paddingBottomRight = [(pn && pn.offsetWidth ? pn.offsetWidth : 0) + 34, 30];
    }
    map.fitBounds(L.latLngBounds(pts), opt);
  }
  function keepInView(s) {
    if (!map.getBounds().contains([s.lat, s.lng])) map.panTo([s.lat, s.lng]);
  }

  function stepAt(t) {
    if (!FC || !FC.steps.length) return null;
    var best = FC.steps[0], bd = 1e9;
    FC.steps.forEach(function (s) { var d = Math.abs(s.t - t); if (d < bd) { bd = d; best = s; } });
    return best;
  }

  /* Origin & age estimate for the run that just finished.

     The old assumption was that a tracked paddy is on day zero of its
     life. It almost never is: it broke off a bed days ago and has been
     fouling in warm water since. This estimates (a) which mapped beds
     the sighting is most consistent with, (b) how many days adrift that
     implies, and (c) how much float time is plausibly left at the
     current water temperature — so the 7-day forecast can say when it
     is forecasting a paddy that may no longer exist.

     Assumptions, all surfaced in the UI: recent currents resemble the
     forecast's drift regime (widened 0.6-1.8x); sources are mapped beds;
     alignment is consistency, not proof. */
  // Takes the steps to reason over so it can run on the cheap centre-only
  // SCOUT pass, before the full ensemble — the ensemble needs the age
  // estimate to seed sinking, and the age estimate needs a drift speed.
  function computeOrigin(stepsIn) {
    ORIGIN = null;
    var box = el('tkOrigin');
    if (box) { box.hidden = true; box.innerHTML = ''; }
    var steps = stepsIn || (FC && FC.steps);
    if (!SITE || !SITE.beds || !steps || steps.length < 6) return;
    // Drift regime off the forecast's own centre track: arc length over
    // the first 48 h (or what exists), in km/day.
    var lastI = 0;
    for (var i = 0; i < steps.length; i++) if (steps[i].t <= 48) lastI = i;
    var arc = 0;
    for (i = 1; i <= lastI; i++) arc += PT.haversineKm(steps[i - 1].lat, steps[i - 1].lng, steps[i].lat, steps[i].lng);
    var speedKmDay = arc / Math.max(1 / 24, steps[lastI].t / 24);
    // Local downstream bearing near the start.
    var d0 = PT.dirAt(steps, Math.min(3, steps.length - 1));
    var flowBrg = (Math.atan2(d0[0], d0[1]) * 180 / Math.PI + 360) % 360;

    var meta = (SITE.frames && SITE.frames[SITE.default_frame] && SITE.frames[SITE.default_frame].meta) || {};
    var tl = (SITE.frames && SITE.frames[SITE.default_frame] && SITE.frames[SITE.default_frame].timeline) || null;
    var tempM = /\((\d+(?:\.\d+)?)\s*°C\)/.exec(meta.why || '');
    var tempC = tempM ? parseFloat(tempM[1]) : null;
    var life = PT.lifespanDays(tempC);

    var cands = PT.sourceCandidates(startLL.lng, startLL.lat, SITE.beds, flowBrg,
                                    speedKmDay, meta.window_days || 24, tl, life.typical);
    var box2 = el('tkOrigin'); if (!box2) return;

    if (!cands.length) {
      ORIGIN = { life: life, out: null };
      box2.innerHTML = '<b>Origin unclear.</b> No mapped beds sit up-current within the ' +
        'model’s ' + (meta.window_days || 24) + '-day window — an older paddy, or an unmapped source. ' +
        lifeLine(life, tempC, null);
      box2.hidden = false;
      return;
    }
    var out = PT.paddyOutlook(cands, life);
    ORIGIN = { life: life, out: out };

    // Several beds on the same island qualify together; merge rows that
    // resolve to the same landmark so the list reads as places.
    var groups = {};
    cands.forEach(function (c) {
      var name = PT.nearestLandmark(c.lng, c.lat) || (c.island ? 'island' : 'coastal');
      var g = groups[name] || (groups[name] = { nmLo: Infinity, nmHi: 0, tLo: Infinity, tHi: 0 });
      var nm = c.distKm / 1.852;
      g.nmLo = Math.min(g.nmLo, nm); g.nmHi = Math.max(g.nmHi, nm);
      g.tLo = Math.min(g.tLo, c.transitLo); g.tHi = Math.max(g.tHi, c.transitHi);
    });
    var rows = Object.keys(groups).map(function (name) {
      var g = groups[name];
      var nmStr = Math.round(g.nmLo) === Math.round(g.nmHi)
        ? Math.round(g.nmLo) + ' nm' : Math.round(g.nmLo) + '–' + Math.round(g.nmHi) + ' nm';
      return '<span class="tk-orow">' + name + ' beds · ' + nmStr + ' up-current · ~' +
        fmtD(g.tLo) + '–' + fmtD(g.tHi) + ' d adrift</span>';
    });
    box2.innerHTML = '<b>Likely origin &amp; age (estimated)</b>' + rows.join('') +
      lifeLine(life, tempC, out) +
      '<span class="tk-ocaveat">From bed positions, shedding history and today’s drift regime. ' +
      'Sinking is modelled forward from this age using the water each run drifts through, ' +
      'charging its pre-sighting life at the temperature where you found it. ' +
      'Consistent-with, not confirmed.</span>';
    box2.hidden = false;
  }
  function fmtD(d) { return d < 1 ? '<1' : String(Math.round(d)); }
  function lifeLine(life, tempC, out) {
    var s = '<span class="tk-olife">';
    s += tempC != null
      ? 'Water ~' + tempC + '°C: typical raft life ~' + (life.typical || PT.LIFE.WARM_D) + ' d '
      : 'Typical raft life ' + PT.LIFE.WARM_D + '–' + PT.LIFE.COOL_D + ' d depending on water temp ';
    s += '(max observed ' + PT.LIFE.MAX_OBSERVED_D[0] + '–' + PT.LIFE.MAX_OBSERVED_D[1] + ' d).';
    if (out) {
      s += out.leftLo <= 0
        ? ' It may already be at the end of its float life — or from a nearer, younger shed.'
        : ' Roughly ' + out.leftLo + '–' + out.leftHi + ' d of float left.';
    }
    return s + '</span>';
  }

  function show(t, fit) {
    var s = stepAt(t); if (!s) return;
    curStep = s;
    var f = fmtLL(s.lat, s.lng);
    el('tkWhen').textContent = s.t === 0 ? 'When you saw it' : clockLabel(s.t) + '  ·  ' + hoursLabel(s.t);
    var tier = s.tier;
    el('tkTier').innerHTML = s.t === 0 ? '' :
      '<span class="tk-dot" style="background:' + TIER_COLOR[tier.key] + '"></span>' +
      '<b>' + tier.label + '</b> · ±' + Math.round(s.crossKm) + ' km either side of the line' +
      ' <span class="tk-sub">(±' + Math.round(s.alongKm) + ' km along it)</span>';
    el('tkDM').textContent = f.dm;
    el('tkDD').textContent = f.dd;
    // "still being tracked", NOT "still floating" — a run leaves the
    // ensemble when it exits the grid, beaches or sinks.
    var inDomainPct = Math.round((s.inDomain != null ? s.inDomain : 1) * 100);
    el('tkNote').textContent = s.t === 0 ? '' :
      (s.t > FC.laneEndH
        ? 'No usable lane this far out — the forecast has fanned out, so the dots are the honest answer. '
        : tier.note + ' ') +
      (inDomainPct < 95 ? inDomainPct + '% of runs still in the forecast area here. ' : '') +
      (FC.truncated && s.t >= FC.hoursCovered - 1
        ? (FC.grounded ? 'Track ends here: the central forecast beaches.'
                       : 'Track ends here: the paddy left the forecast area.') : '') +
      beachNote(s) + sinkNote(s) + thinNote(s) + ageNote(s.t);
    el('tkMini').textContent = s.t === 0 ? 'now' : shortClock(s.t) + ' · ±' + Math.round(s.crossKm) + ' km';
    el('tkScrub').value = s.t;
    syncPickersTo(s.t);
    draw(s);
    if (fit) fitResult(s); else keepInView(s);
  }

  // The forecast positions are conditional on the paddy still floating.
  // Once its estimated age at the selected day passes the typical raft
  // life for this water, say so — the day-zero assumption was the lie.
  // Grounding is part of the forecast, not a footnote: past ~5% beached,
  // say how much of the ensemble is on a shore, and name the shore.
  function beachNote(s) {
    if (!s.beachedFrac || s.beachedFrac < 0.05) return '';
    var pct = Math.round(s.beachedFrac * 100);
    var name = null;
    if (s.beached && s.beached.length) {
      var mx = 0, my = 0;
      s.beached.forEach(function (b) { mx += b[0]; my += b[1]; });
      name = PT.nearestLandmark(mx / s.beached.length, my / s.beached.length);
    }
    return ' ' + pct + '% of runs have beached by now' + (name ? ' — nearest shore ' + name : '') + '.';
  }

  // Sinking is a forecast output now, not a caveat. Report it once it is
  // material, and name the mechanism — divers reasonably assume a paddy
  // that drifts is a paddy that persists.
  function sinkNote(s) {
    if (!s.sunkFrac || s.sunkFrac < 0.05) return '';
    var pct = Math.round(s.sunkFrac * 100);
    return ' ' + pct + '% of runs have sunk by now (fouling ballast in warm water).';
  }
  // The corridor percentiles are computed from the runs still floating.
  // Once few remain, the width is sampling noise dressed as precision.
  var MIN_LIVE_FOR_WIDTH = 12;
  function thinNote(s) {
    if (s.liveCount == null || s.liveCount >= MIN_LIVE_FOR_WIDTH) return '';
    return ' Only ' + s.liveCount + ' of ' + PT.consts.N_MEMBERS +
      ' runs are still afloat here, so treat the width as indicative, not measured.';
  }

  function ageNote(t) {
    if (!ORIGIN || !ORIGIN.out) return '';
    var typ = ORIGIN.life.typical != null ? ORIGIN.life.typical : PT.LIFE.WARM_D;
    var ageMin = ORIGIN.out.ageLo + t / 24;
    if (ageMin <= typ) return '';
    return ' By this day it would be ≥' + Math.round(ageMin) +
      ' d adrift, past typical raft life for this water.';
  }

  function t0Of() { return (FC && FC.t0) || Date.now(); }

  /* ---- day / time pickers -------------------------------------------
     Day is a CALENDAR day (Today, Tomorrow, Sat Oct 10), Time is the
     clock hour you would be on the water. The old Day picker counted
     24-hour blocks from the moment you pressed run, so at 4 PM "Today
     04:00" meant 4 AM TOMORROW, while the label beside it said Thu. */
  var DAYS = [];   // local-midnight ms of each calendar day the forecast touches
  function midnight(ms, addDays) {
    var d = new Date(ms); d.setHours(0, 0, 0, 0);
    if (addDays) d.setDate(d.getDate() + addDays);
    return d.getTime();
  }
  // Forecast steps sit at whole hours after the SIGHTING (4:34, 5:34…),
  // so a clock hour maps to the first step inside it: 7 AM -> 7:34, never
  // the 6:34 that rounding to the nearest step would pick.
  function tFor(k, h) {
    var d = new Date(DAYS[k]); d.setHours(h, 0, 0, 0);   // DST-safe
    return Math.max(0, Math.ceil((d.getTime() - t0Of()) / 3600e3 - 1e-9));
  }
  function hourOk(k, h) {
    var d = new Date(DAYS[k]); d.setHours(h, 59, 59, 999);
    return d.getTime() >= t0Of() && tFor(k, h) <= FC.hoursCovered;
  }
  function buildPickers() {
    var t0 = t0Of(), end = t0 + FC.hoursCovered * 3600e3;
    DAYS = [];
    for (var k = 0; k < 10; k++) { var m = midnight(t0, k); if (m > end) break; DAYS.push(m); }
    var dsel = el('tkDay'); dsel.innerHTML = '';
    DAYS.forEach(function (m, i) {
      var o = document.createElement('option'); o.value = i;
      o.textContent = i === 0 ? 'Today' : i === 1 ? 'Tomorrow'
        : new Date(m).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
      dsel.appendChild(o);
    });
    var hsel = el('tkHour'); hsel.innerHTML = '';
    for (var h = 0; h < 24; h++) {
      var oh = document.createElement('option'); oh.value = h;
      oh.textContent = new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: 'numeric' });
      hsel.appendChild(oh);
    }
  }
  function refreshHours(k) {
    var opts = el('tkHour').options;
    for (var h = 0; h < opts.length; h++) opts[h].disabled = !hourOk(k, h);
  }
  function syncPickersTo(t) {
    if (!DAYS.length) return;
    var when = new Date(t0Of() + t * 3600e3), ms = when.getTime(), k = 0;
    for (var i = 0; i < DAYS.length; i++) if (DAYS[i] <= ms) k = i;
    el('tkDay').value = String(k);
    refreshHours(k);
    el('tkHour').value = String(when.getHours());
  }
  function syncFromPickers(e) {
    var k = parseInt(el('tkDay').value, 10) || 0;
    var h = parseInt(el('tkHour').value, 10) || 0;
    // Changing the DAY keeps the clock hour when that hour exists on the
    // new day, else snaps to the nearest hour the forecast covers.
    if (!hourOk(k, h) && e && e.target && e.target.id === 'tkDay') {
      var best = null;
      for (var d = 0; d < 24 && best == null; d++) {
        if (h - d >= 0 && hourOk(k, h - d)) best = h - d;
        else if (h + d < 24 && hourOk(k, h + d)) best = h + d;
      }
      if (best != null) h = best;
    }
    show(Math.min(FC ? FC.hoursCovered : 168, tFor(k, h)));
  }
  // Where the result view lands: tomorrow 7 AM, the morning you would
  // actually run out to it. Falls back to +24 h if that is not covered.
  function defaultT() {
    if (DAYS.length > 1 && hourOk(1, 7)) return tFor(1, 7);
    return Math.min(24, FC.hoursCovered);
  }

  /* ---- step 1: position entry ---------------------------------------- */
  function inArea(ll) {
    var b = SITE && SITE.bounds;
    return !b || (ll.lat >= b[0][0] && ll.lat <= b[1][0] && ll.lng >= b[0][1] && ll.lng <= b[1][1]);
  }
  function placePin(ll, pan) {
    if (!pin) {
      pin = L.marker([ll.lat, ll.lng], { draggable: true, zIndexOffset: 1200, keyboard: false,
        icon: L.divIcon({ className: '', html: '<div class="tk-pin"></div>',
                          iconSize: [22, 22], iconAnchor: [11, 11] }) })
        .bindTooltip('Paddy here · drag to adjust', { direction: 'top', offset: [0, -12] })
        .addTo(map);
      pin.on('dragend', function () {
        var p = pin.getLatLng();
        el('tkLL').value = p.lat.toFixed(4) + ', ' + p.lng.toFixed(4);
        lastVia = 'tap';
        onInput(true);
      });
    } else {
      pin.setLatLng([ll.lat, ll.lng]);
    }
    if (pan && !map.getBounds().pad(-0.15).contains([ll.lat, ll.lng])) map.panTo([ll.lat, ll.lng]);
  }
  function removePin() { if (pin) { map.removeLayer(pin); pin = null; } }

  // Parse as you type and say what was read, BEFORE the run. Several
  // plotter formats look alike, and a misparse would silently forecast
  // the wrong piece of ocean.
  function onInput(fromMap) {
    var v = el('tkLL').value.trim();
    var ll = v ? parseLL(v) : null;
    var echo = el('tkEcho');
    pending = null;
    if (!v) { echo.textContent = ''; echo.className = 'tk-echo'; removePin(); }
    else if (!ll) {
      echo.textContent = 'Can’t read that yet. Try 32 56.000 117 52.000 or 32.9333, -117.8667';
      echo.className = 'tk-echo bad'; removePin();
    } else if (!inArea(ll)) {
      echo.textContent = 'Reads as ' + fmtLL(ll.lat, ll.lng).dm +
        ', which is outside the forecast area (Southern California Bight).';
      echo.className = 'tk-echo bad'; removePin();
    } else {
      pending = ll;
      echo.textContent = 'Reads as ' + fmtLL(ll.lat, ll.lng).dm;
      echo.className = 'tk-echo ok';
      placePin(ll, !fromMap);
    }
    el('tkRun').disabled = !pending || busy;
  }
  function useGps() {
    if (!navigator.geolocation) { setMsg('This browser has no location access.', true); return; }
    setMsg('Getting a GPS fix…');
    navigator.geolocation.getCurrentPosition(function (p) {
      if (mode !== 'enter') return;
      el('tkLL').value = p.coords.latitude.toFixed(4) + ', ' + p.coords.longitude.toFixed(4);
      lastVia = 'gps';
      setMsg('GPS fix, accurate to about ' + Math.round(p.coords.accuracy) + ' m.');
      onInput(false);
    }, function (err) {
      setMsg(err && err.code === 1
        ? 'Location permission was denied. Tap the map or type the position instead.'
        : 'Could not get a GPS fix. Tap the map or type the position instead.', true);
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }

  /* ---- run ----------------------------------------------------------- */
  var runSeq = 0;
  var FORCING_MAX_AGE_MS = 3600e3;   // reload currents/wind after an hour
  function run() {
    if (busy || !pending) return;
    var ll = pending, token = ++runSeq;
    var btn = el('tkRun');
    busy = true; btn.disabled = true; btn.textContent = 'Loading currents and wind…';
    setMsg('');
    logRun(ll, lastVia);
    function done() { busy = false; btn.textContent = 'Forecast drift'; btn.disabled = !pending; }
    function fail(s) { done(); setMsg(s, true); }
    // Forcing times are hours from the t0 it was loaded at, so a cached
    // set is reused only while fresh, and the forecast is stamped with
    // ITS t0 (stamping it with "now" offset every clock label by however
    // long the page had been open).
    var job = (F && Date.now() - F.t0 < FORCING_MAX_AGE_MS)
      ? Promise.resolve(F)
      : (function (t0) {
          return PT.loadForcing(t0).then(function (f) { f.t0 = t0; return f; });
        })(Date.now());
    job.then(function (forcing) {
      if (token !== runSeq) return;   // closed meanwhile; exit() already reset state
      F = forcing;
      if (!F.rtofs.length && !F.surface.length) return fail('No forecast current data available right now.');
      btn.textContent = 'Running drift ensemble…';
      // Yield so the label paints before the (synchronous) ensemble.
      setTimeout(function () {
        if (token !== runSeq) return;
        var fc;
        try {
          // SCOUT: one cheap centre-only track (no ensemble) purely to
          // get a drift speed and bearing, so the origin model can date
          // the paddy. That age then seeds the ensemble's sinking, which
          // is why this runs before the real forecast rather than after.
          // Identical call to the one forecast() makes for its centre
          // track (same seed, unperturbed, never sinks), so the age this
          // produces is the same age the panel finally displays — the
          // number driving sinking and the number shown cannot diverge.
          // computeOrigin reads only position and time, so the spread
          // fields the real steps carry are not needed here.
          startLL = ll;
          var scout = PT.integrate(F, ll.lng, ll.lat, 168, false, 1);
          var scoutSteps = scout.map(function (pt) {
            return { t: pt.t, lat: pt.lat, lng: pt.lng };
          });
          computeOrigin(scoutSteps);
          var ageRange = ORIGIN && ORIGIN.out
            ? { lo: ORIGIN.out.ageLo, hi: ORIGIN.out.ageHi } : null;
          fc = PT.forecast(F, ll.lng, ll.lat, 168, { startAgeDays: ageRange });
          fc.t0 = F.t0;
        } catch (e) { return fail('Drift failed: ' + e.message); }
        if (!fc.steps || fc.steps.length < 2) {
          return fail('That position has no current data. Is it on land or outside the map?');
        }
        done();
        FC = fc;
        showResult();
      }, 30);
    }).catch(function (e) {
      if (token === runSeq) fail('Could not load forecast data: ' + e.message);
    });
  }

  /* ---- step 2: result ------------------------------------------------ */
  function showResult() {
    removePin();
    setMode('result');
    setMsg('');
    var days = Math.floor(FC.hoursCovered / 24);
    var src = F.sources || {};
    el('tkSrc').textContent = 'Forcing: ' + (src.rtofs || 0) + ' ocean-model + ' +
      (src.surface || 0) + ' surface-current + ' + (src.wind || 0) + ' wind fields' +
      (F.notes && F.notes.length ? ' — ' + F.notes.join('; ') : '');
    var from = el('tkFrom');
    from.textContent = fmtLL(startLL.lat, startLL.lng).dm;
    from.title = 'Read as ' + (startLL.how || 'position');
    el('tkSum').textContent = FC.grounded
      ? 'Tracked ' + days + ' day(s): the central forecast beaches there. Grey dots are runs already ashore.'
      : FC.truncated
      ? 'Tracked ' + days + ' day(s): it drifts out of the forecast area after that.'
      : '7-day drift from a ' + PT.consts.N_MEMBERS + '-run ensemble. Drag the slider to move through time.';
    el('tkScrub').max = FC.hoursCovered;
    buildPickers();
    computeOrigin(FC.steps);      // re-render against the real track
    // Details start open on a desktop; on a phone they would push the
    // map off screen, so they wait behind the disclosure.
    el('tkMore').open = !isPhone();
    show(defaultT(), true);
  }

  /* ---- state --------------------------------------------------------- */
  // Desktop: hang the panel just under the toolbar, whose height changes
  // when it wraps on narrower windows. Phone: CSS docks it to the bottom.
  function placePanel() {
    var bar = document.querySelector('.bar');
    if (isPhone() || !bar) { wrap.style.top = ''; wrap.style.maxHeight = ''; return; }
    var top = Math.round(bar.getBoundingClientRect().bottom) + 8;
    wrap.style.top = top + 'px';
    wrap.style.maxHeight = 'calc(100vh - ' + (top + 20) + 'px)';
  }
  function setMode(m) {
    mode = m;
    wrap.hidden = m === 'closed';
    wrap.classList.remove('min');
    wrap.setAttribute('data-mode', m);
    el('tkEnter').hidden = m !== 'enter';
    el('tkResult').hidden = m !== 'result';
    document.body.classList.toggle('tk-on', m !== 'closed');
    map.getContainer().classList.toggle('tk-picking', m === 'enter');
    if (openBtn) openBtn.classList.toggle('on', m !== 'closed');
    if (m !== 'closed') placePanel();
  }
  function clearForecast() {
    runSeq++;                     // orphan any run still in flight
    FC = null; ORIGIN = null; curStep = null; DAYS = [];
    if (layer) layer.clearLayers();
  }
  // Fresh position entry. `value` pre-fills it (Edit keeps the old one).
  function startEnter(value) {
    clearForecast();
    setMode('enter');
    setMsg('');
    el('tkLL').value = value || '';
    onInput(false);
    if (!value && !isPhone()) el('tkLL').focus();
  }
  function open() { if (mode === 'closed') startEnter(''); }
  // Exit from any state: hide the panel AND clear the map, so closing
  // means closed — not a forecast left painted with no panel to explain it.
  function exit() {
    clearForecast();
    removePin();
    pending = null;
    busy = false;
    var b = el('tkRun'); if (b) { b.textContent = 'Forecast drift'; }
    setMode('closed');
    setMsg('');
    try {
      var u = new URL(location.href);
      if (u.searchParams.has('track')) {
        u.searchParams.delete('track');
        history.replaceState(null, '', u.pathname + u.search + u.hash);
      }
    } catch (e) { /* cosmetic only */ }
  }
  // app.js asks before dropping its own GPS waypoint on a map click: while
  // the tracker is waiting for a position, the click belongs to it.
  function wantsClick() { return mode === 'enter'; }

  function init(m, data) {
    map = m;
    SITE = data || null;   // beds + shedding hindcast, for the origin estimate
    layer = L.layerGroup().addTo(map);
    laneR = makePane('tkLane', 405, 0.14);
    selR = makePane('tkSel', 406, 0.32);
    wrap = document.createElement('div');
    wrap.className = 'tkpanel'; wrap.id = 'tkpanel'; wrap.hidden = true;
    wrap.innerHTML = panelHTML();
    document.body.appendChild(wrap);
    // Keep map gestures from firing through the panel (a tap on a button
    // would otherwise also count as a map tap and move the pin).
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);

    openBtn = document.createElement('button');
    openBtn.id = 'tkOpen'; openBtn.className = 'logbtn tkopen';
    openBtn.title = 'Forecast where a paddy you found will drift';
    openBtn.textContent = '◎ Track a paddy';
    var bar = document.querySelector('.bar');
    if (bar) bar.appendChild(openBtn);
    openBtn.onclick = function () { if (mode === 'closed') open(); else exit(); };

    el('tkClose').onclick = exit;
    el('tkDone').onclick = exit;
    el('tkAnother').onclick = function () { startEnter(''); };
    el('tkEdit').onclick = function () {
      var keep = startLL ? startLL.lat.toFixed(4) + ', ' + startLL.lng.toFixed(4) : '';
      var via = lastVia;
      startEnter(keep);
      lastVia = via;
    };
    el('tkMin').onclick = function () { wrap.classList.toggle('min'); };
    // Phone: the whole header is the handle that folds the sheet away.
    el('tkHead').addEventListener('click', function (e) {
      if (e.target.closest('button') || !isPhone()) return;
      wrap.classList.toggle('min');
    });
    window.addEventListener('resize', function () { if (mode !== 'closed') placePanel(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && mode !== 'closed') exit();
    });

    el('tkRun').onclick = run;
    el('tkLL').addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); });
    el('tkLL').addEventListener('input', function () { lastVia = 'typed'; setMsg(''); onInput(false); });
    if (!navigator.geolocation) el('tkGps').hidden = true;
    el('tkGps').onclick = useGps;
    el('tkScrub').addEventListener('input', function () { show(parseInt(this.value, 10)); });
    el('tkDay').addEventListener('change', syncFromPickers);
    el('tkHour').addEventListener('change', syncFromPickers);
    el('tkCopy').onclick = function () {
      var s = curStep; if (!s) return;
      var f = fmtLL(s.lat, s.lng);
      var txt = 'Paddy drift forecast for ' + clockLabel(s.t) + ' (' + hoursLabel(s.t) + ')\n' +
        f.dm + '  (' + f.dd + ')\n' +
        'Corridor ±' + Math.round(s.crossKm) + ' km either side, ±' +
        Math.round(s.alongKm) + ' km along the line — ' + s.tier.label + '. ' +
        s.tier.note + '\nModelled drift, not an observation.';
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () {
        el('tkCopy').textContent = '✓'; setTimeout(function () { el('tkCopy').textContent = 'Copy'; }, 1500);
      });
    };
    map.on('click', function (e) {
      if (!wantsClick()) return;
      el('tkLL').value = e.latlng.lat.toFixed(4) + ', ' + e.latlng.lng.toFixed(4);
      lastVia = 'tap';
      setMsg('');
      onInput(true);
    });
    // Stroke widths are screen pixels, so the buffered shapes are redrawn
    // for the new scale whenever the zoom settles.
    map.on('zoomend', function () { if (mode === 'result' && curStep) draw(curStep); });

    // Deep link from the main site's tools menu: /paddies/?track=1 lands
    // with the tracker already open, so "Track a paddy" on the phone is
    // one tap, not tap-then-find-the-button.
    if (location.search.indexOf('track=1') !== -1) open();
  }

  return { init: init, parseLL: parseLL, hoursLabel: hoursLabel,
           wantsClick: wantsClick, open: open, exit: exit };
})();
