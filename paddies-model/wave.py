"""Per-bed wave-energy DOSE (canopy-dynamics Phase 1).

Replaces the crude peak-Hs swell term with the three things the wave-biomechanics
literature says actually matter for kelp detachment:
  * ENERGY, not height: Hs^2 * Tp (long-period groundswell hits harder)
  * per-bed DIRECTIONAL exposure (a swell only loads beds open to its bearing)
  * DURATION: integrated over the storm window (fatigue failure, not one peak)
Grounding: Seymour et al. 1989 (ECSS 28:277); Mach 2009/2011 (fatigue);
Bell et al. 2015 / Burrows (exposure). Shared by the P1 detachment term and the
P2 reservoir's shed flux. `profile` comes from exposure.build_profiles().
"""
from __future__ import annotations

import numpy as np

import config
import exposure as exposure_mod


def bed_dose(forcing, profile, blng, blat, end_h, window_days=None):
    """Mean exposure-weighted wave energy above threshold over the `window_days`
    ending at `end_h` (hours into the forcing). Units: arbitrary energy (Hs^2*Tp)
    scaled by exposure in [0,1]; 0 when the bed is sheltered or the sea is calm."""
    window_days = config.WAVE_WINDOW_DAYS if window_days is None else window_days
    h0 = end_h - window_days * 24.0
    vals = []
    h = h0
    while h <= end_h + 1e-9:
        hs = forcing.sample_scalar("hs", h, blat, blng)
        tp = forcing.sample_scalar("tp", h, blat, blng)
        dp = forcing.sample_scalar("dp", h, blat, blng)
        if hs == hs and tp == tp and tp > 0:
            energy = hs * hs * tp
            ex = exposure_mod.exposure(profile, dp) if dp == dp else 1.0
            vals.append(ex * max(0.0, energy - config.WAVE_E_CRIT))
        h += 6.0
    return float(np.mean(vals)) if vals else 0.0
