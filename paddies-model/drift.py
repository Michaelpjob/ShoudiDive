"""Lagrangian drift with the full paddy lifecycle.

Each particle carries a detachment WEIGHT (how much kelp that bed shed
that day). As it drifts it accumulates a SINK hazard from the water
temperature along its path (warm water sinks paddies fast). Its fate is:

  * beached  — it stepped onto land (washed up); leaves the fishery
  * sunk     — survival fell below threshold (waterlogged); gone
  * floating — still afloat offshore => FINDABLE. Its findability weight
               is detachment_weight * survival.

Island-sourced tracks are also returned to show the "pushed out from the
islands" transport.
"""
from __future__ import annotations

import math

import numpy as np

import config
import beds as beds_mod
import geo


def _hazard_per_day(sst, age_days=0.0):
    """Epibiont-ballast sink hazard/day. A fouling/senescence baseline that GROWS
    with age (bryozoan load accretes toward the ~40%-biomass sinking threshold),
    scaled by a warm-water FOULING-RATE multiplier (warmer -> foulers grow faster
    -> earlier sinking; Graiff/Rothausler 2016). Warm water is NOT heat-kill at
    SoCal summer SST; a separate steep term models physiological collapse only
    above SINK_HOT_C (~24 C, Rothausler 2009)."""
    fouling = config.SINK_BASE_PER_DAY * (1.0 + age_days / config.SINK_AGE_TAU_DAYS)
    if sst != sst:  # NaN SST -> no warm signal
        return fouling
    warm_mult = 1.0 + config.SINK_WARM_PER_C * max(0.0, sst - config.SINK_T0_C)
    hot = config.SINK_HOT_PER_C * max(0.0, sst - config.SINK_HOT_C)
    return fouling * warm_mult + hot


def _quality(sst):
    """Fish-holding QUALITY of a still-floating paddy from the water it now sits
    in: full in cool/temperate water, ramping to 0 as it sits in warm water that
    degrades the raft and drives fish off (Rothausler 2009: >24 C collapse). This
    surfaces the literature's #1 signal (temperature) into the opportunity, not
    just the float/sink binary -- a cool 40-day paddy holds far more than a warm
    5-day one even when both are 'floating'."""
    if sst != sst:
        return 1.0
    if sst <= config.QUAL_T0_C:
        return 1.0
    return max(0.0, (config.QUAL_HOT_C - sst) / (config.QUAL_HOT_C - config.QUAL_T0_C))


def _stokes_kmh(hs, tp, dp):
    """Deep-water surface Stokes drift (km/h), toward the wave-propagation
    direction. Us = (2*pi^3/g)*Hs^2/Tp^3; swell Dp is 'from' -> go to Dp+180."""
    if hs != hs or tp != tp or dp != dp or tp < 2.0 or hs <= 0:
        return 0.0, 0.0
    us_kmh = config.STOKES_COEF * (2.0 * math.pi ** 3 / 9.81) * (hs ** 2) / (tp ** 3) * 3.6
    d = math.radians(dp + 180.0)
    return us_kmh * math.sin(d), us_kmh * math.cos(d)


def _velocity(forcing, h, lat, lng):
    """Full paddy velocity (km/h) = blended current + Stokes wave drift + windage."""
    cu, cv = forcing.sample(h, lat, lng)
    su, sv = _stokes_kmh(forcing.sample_scalar("hs", h, lat, lng),
                         forcing.sample_scalar("tp", h, lat, lng),
                         forcing.sample_scalar("dp", h, lat, lng))
    wu = forcing.sample_scalar("wind_u", h, lat, lng)
    wv = forcing.sample_scalar("wind_v", h, lat, lng)
    wu = 0.0 if wu != wu else wu
    wv = 0.0 if wv != wv else wv
    a = config.WINDAGE_ALPHA
    return cu + su + a * wu, cv + sv + a * wv


def advect(forcing, landmask, lng0, lat0, t_start_h, t_end_h, rng=None, record_track=False):
    """RK2 advection through current + Stokes + windage, with eddy diffusion
    and temperature/age sinking. Returns (lng, lat, fate, survival, track)."""
    lat, lng, h, hazard = lat0, lng0, t_start_h, 0.0
    track = [(round(lng, 4), round(lat, 4))] if record_track else None
    diff_km = math.sqrt(2.0 * config.DIFFUSION_K_M2S * config.DT_HOURS * 3600.0) / 1000.0
    while h < t_end_h - 1e-9:
        step = min(config.DT_HOURS, t_end_h - h)
        kpd_lat, kpd_lng = geo.km_per_deg(lat)
        u1, v1 = _velocity(forcing, h, lat, lng)
        lat_m = lat + 0.5 * step * v1 / kpd_lat
        lng_m = lng + 0.5 * step * u1 / max(kpd_lng, 1e-6)
        u2, v2 = _velocity(forcing, h + 0.5 * step, lat_m, lng_m)
        nlat = lat + step * v2 / kpd_lat
        nlng = lng + step * u2 / max(kpd_lng, 1e-6)
        if rng is not None and diff_km > 0:
            s = diff_km * math.sqrt(step / config.DT_HOURS)
            nlat += (rng.standard_normal() * s) / kpd_lat
            nlng += (rng.standard_normal() * s) / max(kpd_lng, 1e-6)
        age_days = max(0.0, h - t_start_h) / 24.0
        hazard += _hazard_per_day(forcing.sample_scalar("sst", h, lat, lng), age_days) * (step / 24.0)
        if landmask.is_land(nlng, nlat):
            return lng, lat, "beached", math.exp(-hazard), track
        lat, lng, h = nlat, nlng, h + step
        if record_track and int(round(h)) % 12 == 0:
            track.append((round(lng, 4), round(lat, 4)))
    surv = math.exp(-hazard)
    fate = "sunk" if surv < config.SINK_DEAD_BELOW else "floating"
    return lng, lat, fate, surv, track


def _nearest_water(landmask, lng, lat, max_km=18.0):
    """Snap an inland-placed bed centroid out to the nearest coastal water.
    Several NAMED_AREAS centroids are town locations that sit inland of the
    coastline; without this, most of their seed disk is on land."""
    if not landmask.is_land(lng, lat):
        return lng, lat
    _, kpd_lng = geo.km_per_deg(lat)
    for r in range(1, int(max_km) + 1):
        for ang in range(0, 360, 20):
            wlng = lng + (r / max(kpd_lng, 1e-6)) * math.sin(math.radians(ang))
            wlat = lat + (r / 111.0) * math.cos(math.radians(ang))
            if not landmask.is_land(wlng, wlat):
                return wlng, wlat
    return lng, lat


def _seed_in_water(rng, blng, blat, radius_km, kpd_lat, kpd_lng, landmask, tries=20):
    for _ in range(tries):
        r = radius_km * math.sqrt(rng.random())
        th = rng.random() * 2 * math.pi
        slat = blat + (r * math.cos(th)) / kpd_lat
        slng = blng + (r * math.sin(th)) / max(kpd_lng, 1e-6)
        if not landmask.is_land(slng, slat):
            return slng, slat
    return None, None


def run_drift(forcing, landmask, detach, now_h=None):
    """now_h = the 'as-of' time (hours since t0) the field is rendered for;
    defaults to the real now. Past frames integrate observed forcing; future
    frames integrate forecast forcing (HFR currents stop at ~now)."""
    if now_h is None:
        now_h = forcing.now_hours()
    rng = np.random.default_rng(config.SEED)
    floating, beached, sunk, beds_fc, tracks = [], [], [], [], []
    float_amount = 0.0
    # area-weight emission: each cell sheds kelp in proportion to its REAL Landsat
    # canopy area (km^2), so the big outer-island forests (San Clemente Is, San
    # Nicolas, Santa Rosa) outweigh the small nearshore pockets -- by measurement,
    # not by a hand-drawn radius (which had badly over-rated Catalina).
    _pow = config.SEED_AREA_POW
    _mean_area = sum(b[5] ** _pow for b in beds_mod.SCB_BEDS) / len(beds_mod.SCB_BEDS)

    for (name, blng, blat, radius_km, is_island, area_km2) in beds_mod.SCB_BEDS:
        kpd_lat, kpd_lng = geo.km_per_deg(blat)
        clng, clat = _nearest_water(landmask, blng, blat)   # snap inland centroids to coastal water
        wmap = detach["per_bed"][name]
        area_factor = (area_km2 ** _pow) / _mean_area
        if not is_island:   # cautious shore-source credit: mainland beds feed the
            area_factor *= getattr(config, "SHORE_SOURCE_BOOST", 1.0)  # nearshore fishery with short, low-attrition drift
        for age in config.RELEASE_AGES_DAYS:
            if age < config.MIN_FINDABLE_AGE_DAYS:
                continue   # fresh sheds still on the source forest aren't findable rafts
            w = wmap[age] * area_factor
            for _ in range(config.PARTICLES_PER_RELEASE):
                slng, slat = _seed_in_water(rng, clng, clat, radius_km, kpd_lat, kpd_lng, landmask)
                if slng is None:
                    continue
                elng, elat, fate, surv, _ = advect(forcing, landmask, slng, slat,
                                                   now_h - age * 24.0, now_h, rng=rng)
                fw = float(w) * surv
                if fate == "floating":   # weight the findable paddy by its current-water quality
                    fw *= _quality(forcing.sample_scalar("sst", now_h, elat, elng))
                rec = {"bed": name, "island": is_island, "age_days": age,
                       "lng": round(elng, 4), "lat": round(elat, 4),
                       "weight": round(float(w), 3), "survival": round(surv, 3),
                       "float_w": round(fw, 3)}
                if fate == "beached":
                    beached.append(rec)
                elif fate == "sunk":
                    sunk.append(rec)
                else:
                    floating.append(rec)
                    float_amount += fw

        # one representative drift track per island (oldest cohort), seeded
        # in WATER near the island — the centroid itself is on land.
        if is_island:
            tlng, tlat = _seed_in_water(rng, clng, clat, radius_km, kpd_lat, kpd_lng, landmask)
            if tlng is None:
                track = None
            else:
                *_, track = advect(forcing, landmask, tlng, tlat,
                                   now_h - max(config.RELEASE_AGES_DAYS) * 24.0, now_h,
                                   record_track=True)
            if track and len(track) > 1:
                tracks.append({"type": "Feature",
                               "geometry": {"type": "LineString", "coordinates": track},
                               "properties": {"bed": name}})
        beds_fc.append({"type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [blng, blat]},
                        "properties": {"bed": name, "island": is_island,
                                       "detach_now": round(float(wmap[0]), 3)}})

    n_all = len(floating) + len(beached) + len(sunk)
    raw_count = (config.HOBDAY_DENSITY_PER_KM2 * config.FISHABLE_AREA_KM2
                 * min(3.0, float_amount / max(config.FLOAT_AMOUNT_REF, 1e-6)))
    est_count = int(round(raw_count / 500.0)) * 500   # round — order-of-magnitude only
    meta = dict(forcing.meta)
    meta.update({
        "n_floating": len(floating), "n_beached": len(beached), "n_sunk": len(sunk),
        "frac_floating": round(len(floating) / max(n_all, 1), 2),
        "frac_beached": round(len(beached) / max(n_all, 1), 2),
        "frac_sunk": round(len(sunk) / max(n_all, 1), 2),
        "float_amount": round(float_amount, 1),
        "est_floating_paddies": est_count,
        "abundance_index": detach["index"], "abundance_band": detach["band"],
        "dominant_driver": detach["dominant"],
        "peak_hs_m": detach["peak_hs_m"], "peak_sst_c": detach["peak_sst_c"],
        "why": detach["why"],
    })
    return {"floating": floating, "beached": beached, "sunk": sunk,
            "beds": beds_fc, "tracks": tracks, "meta": meta}
