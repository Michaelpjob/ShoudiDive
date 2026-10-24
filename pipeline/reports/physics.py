"""Small, dependency-free physics for the report.

* Near-bottom wave orbital velocity from linear wave theory, the number behind
  "how hard is the swell stirring the bottom at dive depth".
* Sunrise / sunset (NOAA solar position equations).
* Moon phase and illuminated fraction (mean synodic month from a reference
  new moon; good to a few percent illumination, plenty for a daily report).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

G = 9.81
SAND_MOTION_M_S = 0.15  # fine sand starts moving at roughly 0.1-0.2 m/s (Komar & Miller 1973)


def wavenumber(period_s: float, depth_m: float) -> float:
    """Solve the linear dispersion relation w^2 = g k tanh(k h) for k."""
    w = 2 * math.pi / period_s
    k = w * w / G  # deep-water first guess
    for _ in range(60):
        f = G * k * math.tanh(k * depth_m) - w * w
        df = G * math.tanh(k * depth_m) + G * k * depth_m / math.cosh(k * depth_m) ** 2
        step = f / df
        k -= step
        if abs(step) < 1e-10:
            break
    return k


def bottom_orbital_velocity(height_m: float, period_s: float, depth_m: float) -> float:
    """Peak near-bottom orbital velocity (m/s) under a wave of height H.

    u_b = pi H / (T sinh(k h)). With H = significant height this is a
    deliberately conservative (upper) reading of the stirring.
    """
    if height_m <= 0 or period_s <= 0 or depth_m <= 0:
        return 0.0
    k = wavenumber(period_s, depth_m)
    return math.pi * height_m / (period_s * math.sinh(k * depth_m))


def combined_bottom_velocity(trains: list[tuple[float, float]], depth_m: float) -> float:
    """Root-sum-square over (height_m, period_s) trains."""
    return math.sqrt(sum(bottom_orbital_velocity(h, t, depth_m) ** 2 for h, t in trains))


# --- Sun -------------------------------------------------------------------

def _julian_day(dt: datetime) -> float:
    return dt.timestamp() / 86400.0 + 2440587.5


def sun_times(day: date, lat: float, lng: float) -> tuple[datetime | None, datetime | None]:
    """UTC sunrise and sunset for a calendar day (NOAA approximation, ~1 min)."""
    def event(rising: bool) -> datetime | None:
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc) - timedelta(hours=lng / 15.0)
        t = noon
        for _ in range(3):
            jd = _julian_day(t)
            jc = (jd - 2451545.0) / 36525.0
            l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
            m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
            e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
            c = (math.sin(math.radians(m)) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
                 + math.sin(math.radians(2 * m)) * (0.019993 - 0.000101 * jc)
                 + math.sin(math.radians(3 * m)) * 0.000289)
            true_long = l0 + c
            omega = 125.04 - 1934.136 * jc
            app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
            mean_obl = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
            obl = mean_obl + 0.00256 * math.cos(math.radians(omega))
            decl = math.degrees(math.asin(math.sin(math.radians(obl)) * math.sin(math.radians(app_long))))
            y = math.tan(math.radians(obl / 2)) ** 2
            eq_time = 4 * math.degrees(
                y * math.sin(2 * math.radians(l0))
                - 2 * e * math.sin(math.radians(m))
                + 4 * e * y * math.sin(math.radians(m)) * math.cos(2 * math.radians(l0))
                - 0.5 * y * y * math.sin(4 * math.radians(l0))
                - 1.25 * e * e * math.sin(2 * math.radians(m)))
            cos_ha = (math.cos(math.radians(90.833)) / (math.cos(math.radians(lat)) * math.cos(math.radians(decl)))
                      - math.tan(math.radians(lat)) * math.tan(math.radians(decl)))
            if abs(cos_ha) > 1:
                return None
            ha = math.degrees(math.acos(cos_ha))
            minutes = 720 - 4 * (lng + (ha if rising else -ha)) - eq_time
            t = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        return t
    return event(True), event(False)


# --- Moon ------------------------------------------------------------------

SYNODIC_DAYS = 29.530588853
_REF_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
_PHASES = [
    (0.0339, "New moon"), (0.2161, "Waxing crescent"), (0.2839, "First quarter"),
    (0.4661, "Waxing gibbous"), (0.5339, "Full moon"), (0.7161, "Waning gibbous"),
    (0.7839, "Last quarter"), (0.9661, "Waning crescent"), (1.01, "New moon"),
]


def moon(at: datetime) -> dict:
    """Phase name, age in days, illuminated fraction (0-1)."""
    age = ((at - _REF_NEW_MOON).total_seconds() / 86400.0) % SYNODIC_DAYS
    frac = age / SYNODIC_DAYS
    illum = (1 - math.cos(2 * math.pi * frac)) / 2
    name = next(n for lim, n in _PHASES if frac < lim)
    return {"phase": name, "age_days": round(age, 1), "illuminated": round(illum, 2)}
