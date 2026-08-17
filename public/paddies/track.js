'use strict';
/* PaddyTrack — "track my paddy": a client-side Lagrangian drift forecast
 * from a GPS position the user actually saw a paddy at.
 *
 * Runs entirely in the browser off data already published for the map
 * (same pattern as the SST break tracer): no pipeline change, no new
 * artifact. It fetches the forecast forcing fields, decodes their UV
 * PNGs, and integrates a particle forward hour by hour.
 *
 * FORCING (all same-origin, already on prod):
 *   /data/currents/  5 days x 5 buckets  surface current, kt, [-1.5,1.5]
 *   /data/wind/      7 days x 5 buckets  wind, [-30,30] kt   -> windage
 *   /data/rtofs/     leads +1/+3/+5/+7d  RTOFS model current, m/s [-2,2]
 *
 * PHYSICS — deliberately identical to the calibrated research prototype
 * (kelp-drift-proto/config.py) so the browser and the offline model
 * cannot silently diverge:
 *   v_paddy = current + WINDAGE_ALPHA * wind      (RK2, dt = 1 h)
 *   WINDAGE_ALPHA = 0.02   floating kelp makes ~2% of wind speed
 *   Currents blend RTOFS (model backbone, reaches +7 d) with the
 *   published surface-current field (HFR-informed detail, +5 d).
 *
 * UNCERTAINTY IS MEASURED, NOT ASSUMED. On 2026-08-16 the two current
 * products we publish for the same water disagreed by 0.166 m/s RMS —
 * against an RTOFS mean speed of 0.179 m/s, i.e. the disagreement is
 * nearly the size of the signal. That number seeds the ensemble spread,
 * so the cone the user sees is the real disagreement between our own
 * best estimates, propagated forward. Resulting 68% radii land near
 * 10-14 km at day 1 and 27-100 km at day 7 — which is why the readout
 * always carries its radius and why the UI stops calling the late days
 * a position at all.
 */

var PT = (function () {

  // ---- physics constants (mirror kelp-drift-proto/config.py) -----------
  var WINDAGE_ALPHA = 0.02;      // floating kelp drifts at ~2% of wind
  var DIFFUSION_K_M2S = 5.0;     // sub-grid eddy diffusivity
  var DT_HOURS = 1.0;
  var BLEND_RTOFS = 0.65;        // model backbone
  var BLEND_SURFACE = 0.35;      // published surface-current detail

  // ---- measured uncertainty (see header) -------------------------------
  var SIGMA_V_MS = 0.166;        // RMS disagreement between our current products
  var DECORR_HOURS = 12;         // error redrawn twice a day
  var N_MEMBERS = 60;

  var KT_TO_MS = 0.514444;
  var MS_TO_KMH = 3.6;

  function kmPerDeg(lat) {
    var r = lat * Math.PI / 180;
    return { lat: 111.132, lng: 111.320 * Math.cos(r) };
  }

  // ---- PNG -> Float32 UV grid -----------------------------------------
  function decodeUV(url, lo, hi) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = function () {
        var w = img.naturalWidth, h = img.naturalHeight;
        var cv = document.createElement('canvas');
        cv.width = w; cv.height = h;
        var ctx = cv.getContext('2d');
        ctx.drawImage(img, 0, 0);
        var d;
        try { d = ctx.getImageData(0, 0, w, h).data; }
        catch (e) { resolve(null); return; }
        var u = new Float32Array(w * h), v = new Float32Array(w * h);
        for (var i = 0; i < w * h; i++) {
          if (d[i * 4 + 3] === 0) { u[i] = NaN; v[i] = NaN; continue; }
          u[i] = lo + (d[i * 4] / 255) * (hi - lo);
          v[i] = lo + (d[i * 4 + 1] / 255) * (hi - lo);
        }
        resolve({ u: u, v: v, w: w, h: h });
      };
      img.onerror = function () { resolve(null); };
      img.src = url;
    });
  }

  // Bilinear sample of a UV grid laid over `bbox`. Row 0 = latMax.
  function sampleUV(g, bbox, lng, lat) {
    if (!g) return null;
    var fx = (lng - bbox.lngMin) / (bbox.lngMax - bbox.lngMin) * (g.w - 1);
    var fy = (bbox.latMax - lat) / (bbox.latMax - bbox.latMin) * (g.h - 1);
    if (!(fx >= 0 && fx <= g.w - 1 && fy >= 0 && fy <= g.h - 1)) return null;
    var x0 = Math.floor(fx), y0 = Math.floor(fy);
    var x1 = Math.min(x0 + 1, g.w - 1), y1 = Math.min(y0 + 1, g.h - 1);
    var tx = fx - x0, ty = fy - y0;
    var su = 0, sv = 0, sw = 0;
    var pts = [[x0, y0, (1 - tx) * (1 - ty)], [x1, y0, tx * (1 - ty)],
               [x0, y1, (1 - tx) * ty], [x1, y1, tx * ty]];
    for (var k = 0; k < 4; k++) {
      var idx = pts[k][1] * g.w + pts[k][0], wgt = pts[k][2];
      if (!isFinite(g.u[idx])) continue;      // NaN = land / no data
      su += g.u[idx] * wgt; sv += g.v[idx] * wgt; sw += wgt;
    }
    if (sw < 0.35) return null;               // mostly land: treat as beached
    return { u: su / sw, v: sv / sw };
  }

  // ---- forcing assembly ------------------------------------------------
  // Each source becomes a time-sorted list of {tHours, grid} where tHours
  // is hours from `t0` (the forecast start).
  function loadForcing(t0) {
    var bbox = null, out = { surface: [], wind: [], rtofs: [], bbox: null, notes: [] };

    return fetch('/data/manifest.json', { cache: 'no-cache' })
      .then(function (r) { return r.json(); })
      .then(function (m) {
        var b = m.bbox;   // [lngMin, latMin, lngMax, latMax]
        bbox = { lngMin: b[0], latMin: b[1], lngMax: b[2], latMax: b[3] };
        out.bbox = bbox;
        var L = m.layers || {};
        var jobs = [];

        // --- published surface currents (kt, direction_to) ---
        if (L.current5d && L.current5d.summary_url) {
          jobs.push(fetch(L.current5d.summary_url, { cache: 'no-cache' })
            .then(function (r) { return r.json(); })
            .then(function (s) {
              var rng = L.current5d.uv_range || [-1.5, 1.5];
              var anchor = Date.parse((s.anchor_date || '') + 'T00:00:00Z');
              var subs = [];
              (s.days || []).forEach(function (day) {
                (day.buckets || []).forEach(function (bk) {
                  if (!bk.uv_url) return;
                  var when = bk.valid_at ? Date.parse(bk.valid_at)
                    : anchor + (day.day * 24 + midHour(bk.hours)) * 3600e3;
                  subs.push(decodeUV(bk.uv_url, rng[0], rng[1]).then(function (g) {
                    if (g) out.surface.push({ t: (when - t0) / 3600e3, g: g, k: KT_TO_MS });
                  }));
                });
              });
              return Promise.all(subs);
            }).catch(function () { out.notes.push('surface currents unavailable'); }));
        }

        // --- wind (kt) for windage ---
        if (L.wind5d && L.wind5d.summary_url) {
          jobs.push(fetch(L.wind5d.summary_url, { cache: 'no-cache' })
            .then(function (r) { return r.json(); })
            .then(function (s) {
              var rng = L.wind5d.uv_range || [-30, 30];
              var anchor = Date.parse((s.anchor_date || s.days[0].date) + 'T00:00:00Z');
              var subs = [];
              (s.days || []).forEach(function (day) {
                (day.buckets || []).forEach(function (bk) {
                  if (!bk.uv_url) return;
                  var when = bk.valid_at ? Date.parse(bk.valid_at)
                    : anchor + (day.day * 24 + midHour(bk.hours)) * 3600e3;
                  subs.push(decodeUV(bk.uv_url, rng[0], rng[1]).then(function (g) {
                    if (g) out.wind.push({ t: (when - t0) / 3600e3, g: g, k: KT_TO_MS });
                  }));
                });
              });
              return Promise.all(subs);
            }).catch(function () { out.notes.push('wind unavailable'); }));
        }

        // --- RTOFS model currents (m/s) — the only forcing past +5 d ---
        if (L.rtofs5d && L.rtofs5d.summary_url) {
          jobs.push(fetch(L.rtofs5d.summary_url, { cache: 'no-cache' })
            .then(function (r) { return r.json(); })
            .then(function (s) {
              var rng = L.rtofs5d.uv_range || [-2, 2];
              var init = Date.parse(s.init_cycle || '');
              var subs = [];
              (s.days || []).forEach(function (day) {
                if (!day.uv_url) return;
                var when = init + (day.lead_hours || day.day_offset * 24) * 3600e3;
                subs.push(decodeUV(day.uv_url, rng[0], rng[1]).then(function (g) {
                  if (g) out.rtofs.push({ t: (when - t0) / 3600e3, g: g, k: 1.0 });
                }));
              });
              return Promise.all(subs);
            }).catch(function () { out.notes.push('RTOFS unavailable'); }));
        }

        return Promise.all(jobs);
      })
      .then(function () {
        ['surface', 'wind', 'rtofs'].forEach(function (k) {
          out[k].sort(function (a, b) { return a.t - b.t; });
        });
        return out;
      });
  }

  function midHour(hrs) {
    if (!hrs || !hrs.length) return 12;
    return hrs.length > 1 ? (hrs[0] + hrs[1]) / 2 : hrs[0];
  }

  // Linear-in-time sample across a sorted field list. Returns m/s.
  function sampleSeries(list, bbox, t, lng, lat) {
    if (!list.length) return null;
    if (t <= list[0].t) return scaled(list[0], bbox, lng, lat);
    if (t >= list[list.length - 1].t) return scaled(list[list.length - 1], bbox, lng, lat);
    for (var i = 0; i < list.length - 1; i++) {
      if (t >= list[i].t && t <= list[i + 1].t) {
        var a = scaled(list[i], bbox, lng, lat), b = scaled(list[i + 1], bbox, lng, lat);
        if (!a) return b; if (!b) return a;
        var f = (t - list[i].t) / Math.max(1e-6, list[i + 1].t - list[i].t);
        return { u: a.u + (b.u - a.u) * f, v: a.v + (b.v - a.v) * f };
      }
    }
    return null;
  }
  function scaled(entry, bbox, lng, lat) {
    var s = sampleUV(entry.g, bbox, lng, lat);
    return s ? { u: s.u * entry.k, v: s.v * entry.k } : null;
  }

  // Full paddy velocity in km/h at (t, lng, lat). null = off-grid / beached.
  function velocity(F, t, lng, lat) {
    var rt = sampleSeries(F.rtofs, F.bbox, t, lng, lat);
    var sf = sampleSeries(F.surface, F.bbox, t, lng, lat);
    var cu = 0, cv = 0, got = false;
    if (rt && sf) { cu = BLEND_RTOFS * rt.u + BLEND_SURFACE * sf.u;
                    cv = BLEND_RTOFS * rt.v + BLEND_SURFACE * sf.v; got = true; }
    else if (rt)  { cu = rt.u; cv = rt.v; got = true; }
    else if (sf)  { cu = sf.u; cv = sf.v; got = true; }
    if (!got) return null;
    var wd = sampleSeries(F.wind, F.bbox, t, lng, lat);
    if (wd) { cu += WINDAGE_ALPHA * wd.u; cv += WINDAGE_ALPHA * wd.v; }
    return { u: cu * MS_TO_KMH, v: cv * MS_TO_KMH };
  }

  // Deterministic per-member pseudo-random (so a track is reproducible).
  function rnd(seed) {
    var s = seed >>> 0;
    return function () {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }
  function gauss(r) {
    var u = Math.max(1e-9, r()), v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  /* Integrate one member. `pert` 0 = unperturbed centre track.
   * Returns an array of {t, lng, lat} at DT_HOURS spacing, ending early
   * if the particle leaves the grid (beached / off-domain). */
  function integrate(F, lng0, lat0, hours, pert, seed) {
    var r = rnd(seed || 1), lng = lng0, lat = lat0, out = [{ t: 0, lng: lng, lat: lat }];
    var eu = 0, ev = 0, nextRedraw = 0;
    var diffKm = Math.sqrt(2 * DIFFUSION_K_M2S * DT_HOURS * 3600) / 1000;
    for (var t = 0; t < hours; t += DT_HOURS) {
      if (pert && t >= nextRedraw) {          // decorrelated velocity error
        eu = gauss(r) * SIGMA_V_MS / Math.SQRT2 * MS_TO_KMH;
        ev = gauss(r) * SIGMA_V_MS / Math.SQRT2 * MS_TO_KMH;
        nextRedraw = t + DECORR_HOURS;
      }
      var k = kmPerDeg(lat);
      var v1 = velocity(F, t, lng, lat);
      if (!v1) break;
      var latM = lat + 0.5 * DT_HOURS * (v1.v + ev) / k.lat;
      var lngM = lng + 0.5 * DT_HOURS * (v1.u + eu) / Math.max(1e-6, k.lng);
      var v2 = velocity(F, t + 0.5 * DT_HOURS, lngM, latM);
      if (!v2) break;
      var nlat = lat + DT_HOURS * (v2.v + ev) / k.lat;
      var nlng = lng + DT_HOURS * (v2.u + eu) / Math.max(1e-6, k.lng);
      if (pert) {                              // sub-grid dispersion
        nlat += gauss(r) * diffKm / k.lat;
        nlng += gauss(r) * diffKm / Math.max(1e-6, k.lng);
      }
      lng = nlng; lat = nlat;
      out.push({ t: t + DT_HOURS, lng: lng, lat: lat });
    }
    return out;
  }

  function haversineKm(aLat, aLng, bLat, bLng) {
    var R = 6371, dp = (bLat - aLat) * Math.PI / 180, dl = (bLng - aLng) * Math.PI / 180;
    var s = Math.sin(dp / 2) * Math.sin(dp / 2) +
      Math.cos(aLat * Math.PI / 180) * Math.cos(bLat * Math.PI / 180) *
      Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
  }

  /* Confidence tiering. These are not vibes — they are where the measured
   * ensemble radius crosses distances that change what a boat should do.
   * A typical run to the paddies is 20-40 nm, so once the search radius
   * approaches that, "position" stops being the right word. */
  function tierFor(radiusKm) {
    if (radiusKm <= 15) return { key: 'search', label: 'Search area',
      note: 'Workable — run to the centre and search outward.' };
    if (radiusKm <= 35) return { key: 'wide', label: 'Wide area',
      note: 'Rough. Direction is meaningful, the exact spot is not.' };
    return { key: 'region', label: 'Region only',
      note: 'Not a waypoint. This is a drift direction over a large area.' };
  }

  /* Public: forecast a paddy seen at (lng, lat) at time `seenAt` (Date).
   * Returns hourly centre positions with a measured 68% radius each. */
  function forecast(F, lng, lat, hours) {
    hours = hours || 168;                    // 7 days
    var centre = integrate(F, lng, lat, hours, false, 1);
    var members = [];
    for (var m = 0; m < N_MEMBERS; m++) {
      members.push(integrate(F, lng, lat, hours, true, 1000 + m * 7919));
    }
    var steps = [];
    for (var i = 0; i < centre.length; i++) {
      var c = centre[i], live = [];
      for (var j = 0; j < members.length; j++) {
        if (members[j].length > i) live.push(members[j][i]);
      }
      var ds = live.map(function (p) { return haversineKm(c.lat, c.lng, p.lat, p.lng); })
                   .sort(function (a, b) { return a - b; });
      var r68 = ds.length ? ds[Math.min(ds.length - 1, Math.floor(ds.length * 0.68))] : 0;
      steps.push({
        t: c.t, lng: c.lng, lat: c.lat,
        radiusKm: r68,
        afloat: live.length / N_MEMBERS,     // fraction still on-grid
        tier: tierFor(r68)
      });
    }
    return {
      steps: steps,
      hoursCovered: steps.length ? steps[steps.length - 1].t : 0,
      truncated: steps.length && steps[steps.length - 1].t < hours
    };
  }

  return {
    loadForcing: loadForcing,
    forecast: forecast,
    integrate: integrate,
    sampleUV: sampleUV,
    tierFor: tierFor,
    haversineKm: haversineKm,
    consts: {
      WINDAGE_ALPHA: WINDAGE_ALPHA, SIGMA_V_MS: SIGMA_V_MS,
      DT_HOURS: DT_HOURS, N_MEMBERS: N_MEMBERS,
      BLEND_RTOFS: BLEND_RTOFS, BLEND_SURFACE: BLEND_SURFACE,
      DIFFUSION_K_M2S: DIFFUSION_K_M2S, DECORR_HOURS: DECORR_HOURS
    }
  };
})();
