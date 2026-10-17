"""Regions of the Ocean Report, by where the boats leave from.

A region is a set of ports. The water, barges, dive sites and NWS zones listed
under it are the ones its boats and divers use; a zone can sit in two regions
(Catalina and San Clemente Island are run from Orange County and Los Angeles,
and San Clemente Island from San Diego too). Ventura and Santa Barbara appear in
the fleet log only: the report's water zones stop at Catalina.
"""
from __future__ import annotations

from .zones import nm_between

REGIONS = [
    {"id": "sd", "name": "San Diego", "ports": ["San Diego", "Mission Bay", "Oceanside"],
     "harbors": [[32.678, -117.236], [32.756, -117.253], [33.204, -117.396]],
     "zones": ["La Jolla to IB", "Del Mar to Oceanside", "Coronados", "San Clemente Is.", "9 Mile / 43", "267 / 209-312",
               "Tanner / Cortes", "South 9 / 302 / 425", "Rockpile / Upper 500"],
     "barges": ["San Diego Bay", "Mission Bay"], "dive_sites": ["La Jolla Cove", "La Jolla Shores", "Point Loma and Sunset Cliffs"],
     "marine_zones": ["PZZ740", "PZZ745", "PMZ009"], "beach_zones": ["CAZ043"], "box": [[32.25, -118.0], [33.3, -117.1]]},
    {"id": "oc", "name": "Orange County", "ports": ["Dana Point", "Newport Beach"],
     "harbors": [[33.456, -117.697], [33.594, -117.879]],
     "zones": ["Dana Point coast", "14 Mile / Avalon Bank", "Catalina", "San Clemente Is.", "267 / 209-312"],
     "barges": ["Dana Point"], "dive_sites": ["Laguna Beach"],
     "marine_zones": ["PZZ655", "PZZ676"], "beach_zones": ["CAZ552"], "box": [[33.1, -118.6], [33.75, -117.45]]},
    {"id": "la", "name": "Los Angeles", "ports": ["Long Beach", "San Pedro", "Redondo Beach", "Marina del Rey"],
     "harbors": [[33.709, -118.250], [33.846, -118.400], [33.966, -118.462]],
     "zones": ["Catalina", "14 Mile / Avalon Bank", "San Clemente Is."],
     "barges": [], "dive_sites": [], "marine_zones": ["PZZ655", "PZZ676"], "beach_zones": [],
     "box": [[33.25, -118.9], [34.05, -118.1]],
     "note": "The report's water zones stop at Catalina; Palos Verdes and Santa Monica Bay are not covered yet."},
    {"id": "vsb", "name": "Ventura and Santa Barbara", "ports": ["Oxnard", "Ventura", "Santa Barbara"],
     "harbors": [[34.160, -119.226], [34.245, -119.266], [34.404, -119.690]],
     "zones": [], "barges": [], "dive_sites": [], "marine_zones": ["PZZ650", "PZZ673"], "beach_zones": [],
     "box": [[33.85, -120.2], [34.5, -119.0]], "fleet_only": True},
]
BY_ID = {r["id"]: r for r in REGIONS}
_PORT = {p: r["id"] for r in REGIONS for p in r["ports"]}


def region_of_port(port: str | None) -> str | None:
    return _PORT.get(port or "")


def region_of_point(lat: float, lng: float, report_only: bool = True) -> str | None:
    """The region whose nearest harbor is closest (field reports carry no port)."""
    best = None
    for r in REGIONS:
        if report_only and r.get("fleet_only"):
            continue
        d = min(nm_between((lat, lng), tuple(h)) for h in r["harbors"])
        if best is None or d < best[0]:
            best = (d, r["id"])
    return best[1] if best else None
