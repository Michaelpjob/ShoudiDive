"""Detachment model — what rips kelp loose, and how much.

Per bed, per day, we read the conditions that detach giant kelp and
turn them into a dimensionless detachment rate. That rate becomes the
SEEDING WEIGHT for particles released from that bed on that day, and
aggregates into a single PADDY ABUNDANCE index for the region.

    detach = BASE_SHED                         # senescence trickle
           + K_WAVE * relu(Hs - HS0) ** HS_POW # storm-wave dislodgement
           + K_WARM * relu(SST - T0)           # thermal-stress shedding

Mechanisms (interpretable, literature-grounded; coefficients un-tuned):
  * wave drag during large swells is the dominant dislodgement force,
    rising steeply once Hs exceeds the holdfast-failure threshold;
  * warm water (>~19-20 C, marine-heatwave / El Nino) degrades fronds
    and pneumatocysts, increasing shedding (and, once afloat, shortening
    paddy life — handled separately as decay).
"""
from __future__ import annotations

import math

import numpy as np

import config
import exposure
import wave


def detach_value(wave_dose, warm_dose):
    """detach = BASE + K_WAVE_E*wave_dose + K_WARM*warm_dose^WARM_POW.
    `wave_dose` = per-bed, exposure-weighted, period-aware (Hs^2*Tp),
    duration-integrated wave-energy dose (wave.bed_dose). `warm_dose` = trailing
    cumulative thermal dose (mean max(0, SST-T0) degC). Both already non-negative.
    NOTE (Phase 1): still a stateless rate; the P2 reservoir makes shedding a
    flux drawn from a depleting vulnerable pool and clamps it to what's left."""
    wd = 0.0 if (wave_dose is None or wave_dose != wave_dose) else max(0.0, wave_dose)
    wave = config.K_WAVE_E * wd
    dose = 0.0 if (warm_dose is None or warm_dose != warm_dose) else max(0.0, warm_dose)
    warm = config.K_WARM * dose ** config.WARM_POW
    return config.BASE_SHED + wave + warm


def _bed_day(forcing, blng, blat, now_h, age):
    """Daily-max Hs and daily-mean SST at a bed for the 24 h leading up to
    a release `age` days ago."""
    h0, h1 = now_h - (age + 1) * 24.0, now_h - age * 24.0
    hs_vals, sst_vals = [], []
    for h in np.arange(h0, h1 + 1e-9, 3.0):
        hv = forcing.sample_scalar("hs", h, blat, blng)
        sv = forcing.sample_scalar("sst", h, blat, blng)
        if hv == hv:
            hs_vals.append(hv)
        if sv == sv:
            sst_vals.append(sv)
    hs_max = max(hs_vals) if hs_vals else float("nan")
    sst_mean = float(np.mean(sst_vals)) if sst_vals else float("nan")
    return hs_max, sst_mean


def _band(idx):
    if idx < 12:
        return "Minimal"
    if idx < 30:
        return "Low"
    if idx < 55:
        return "Moderate"
    if idx < 78:
        return "High"
    return "Extreme"


def _why(band, dominant, peak_hs, peak_sst):
    hs = "n/a" if peak_hs is None else f"{peak_hs:.1f} m"
    sst = "n/a" if peak_sst is None else f"{peak_sst:.1f}°C"
    if band in ("Minimal", "Low"):
        return (f"{band} — calm seas (peak {hs}) and cool water ({sst}); "
                f"little kelp is breaking loose, so few paddies.")
    drv = (f"a {hs} swell tearing kelp loose" if dominant == "swell"
           else f"warm water ({sst}) stressing and shedding kelp")
    return (f"{band} — {drv}; expect fresh paddies seeding offshore from the "
            f"firing beds.")


def compute(forcing, beds, now_h, ages, thermal=None, profiles=None):
    """`thermal` = a forcing.ThermalHistory for the cumulative warm-shed dose.
    `profiles` = exposure.build_profiles(beds) for the per-bed wave-energy dose
    (built here if None). Both degrade gracefully (thermal->instantaneous SST;
    profiles->fully-exposed) so the sweep harness and offline paths still run."""
    if profiles is None:
        profiles = exposure.build_profiles(beds)
    per_bed = {}
    peak_hs = peak_sst = float("nan")
    peak_warm_dose = peak_wave_dose = 0.0
    age_hs = {a: [] for a in ages}      # for the shedding-event timeline
    age_w = {a: [] for a in ages}
    for bed in beds:
        name, blng, blat = bed[0], bed[1], bed[2]
        prof = profiles.get(name)
        wmap = {}
        for age in ages:
            hs_max, sst_mean = _bed_day(forcing, blng, blat, now_h, age)
            if thermal is not None:
                warm_dose = thermal.dose(blat, blng, config.T0_C, age,
                                         config.WARM_DOSE_WINDOW_DAYS)
            else:
                warm_dose = max(0.0, sst_mean - config.T0_C) if sst_mean == sst_mean else 0.0
            wave_dose = wave.bed_dose(forcing, prof, blng, blat, now_h - age * 24.0)
            wmap[age] = detach_value(wave_dose, warm_dose)
            age_w[age].append(wmap[age])
            peak_warm_dose = max(peak_warm_dose, warm_dose)
            peak_wave_dose = max(peak_wave_dose, wave_dose)
            if hs_max == hs_max:
                age_hs[age].append(hs_max)
                peak_hs = hs_max if peak_hs != peak_hs else max(peak_hs, hs_max)
            if sst_mean == sst_mean:
                peak_sst = sst_mean if peak_sst != peak_sst else max(peak_sst, sst_mean)
        per_bed[name] = wmap

    # Regional shedding-event timeline: how much each past day shed (mean Hs
    # that day -> shed intensity). A spike = a storm/swell that broke kelp loose.
    timeline = [{"days_ago": a,
                 "hs_m": round(float(np.mean(age_hs[a])), 1) if age_hs[a] else None,
                 "shed": round(float(np.mean(age_w[a])), 2)} for a in ages]

    all_w = [w for bd in per_bed.values() for w in bd.values()]
    mean_detach = float(np.mean(all_w)) if all_w else 0.0
    index = 100.0 * (1.0 - math.exp(-mean_detach / config.ABUND_SCALE))

    wave_term = config.K_WAVE_E * peak_wave_dose
    warm_term = config.K_WARM * peak_warm_dose ** config.WARM_POW
    dominant = "swell" if wave_term >= warm_term else "warm water"
    band = _band(index)
    ph = round(peak_hs, 2) if peak_hs == peak_hs else None
    ps = round(peak_sst, 1) if peak_sst == peak_sst else None
    return {
        "per_bed": per_bed,
        "index": round(index, 1),
        "band": band,
        "dominant": dominant,
        "peak_hs_m": ph,
        "peak_sst_c": ps,
        "mean_detach": round(mean_detach, 3),
        "why": _why(band, dominant, ph, ps),
        "timeline": timeline,
    }
