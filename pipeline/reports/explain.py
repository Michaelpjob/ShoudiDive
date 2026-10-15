"""Evidence blocks: the data a reader needs to understand a verdict.

Each block is {kind, label, tag, source, ...}: "text" (an agency's own
words), "obs" (a measurement right now), "series" (a timeline), or "note" (a
comparison we computed, such as our model disagreeing with NWS). Blocks are
attached to verdict days; the page shows them under "The data behind this".
"""
from __future__ import annotations

from . import evidence as E
from . import thresholds as T
from .sources import parse_iso


def _hour(iso_or_dt, tz) -> str:
    dt = parse_iso(iso_or_dt) if isinstance(iso_or_dt, str) else iso_or_dt
    loc = dt.astimezone(tz)
    return f"{loc.hour % 12 or 12} {'AM' if loc.hour < 12 else 'PM'}"


def buoy_line(row: dict) -> str:
    parts = []
    if row.get("wave_ft") is not None:
        parts.append(f"waves {row['wave_ft']} ft")
    sw = row.get("swell")
    if sw and sw.get("ft") is not None:
        parts.append(f"swell {sw['ft']} ft at {round(sw['s'])} s {sw.get('dir') or ''}".strip())
    ww = row.get("wind_waves")
    if ww and ww.get("ft") is not None:
        parts.append(f"wind waves {ww['ft']} ft at {round(ww['s'])} s")
    if row.get("wind"):
        w = row["wind"]
        parts.append(f"wind {w['kt']} kt from {E.compass(w.get('from_deg'))}" + (f", gusts {w['gust_kt']}" if w.get("gust_kt") else ""))
    if row.get("temp_f") is not None:
        parts.append(f"water {row['temp_f']}°F")
    return ", ".join(parts)


def alert_blocks(alerts: list[dict], zones: list[str], window, overlaps, events: set[str] | None = None) -> list[dict]:
    out = []
    for a in alerts or []:
        if events is not None and a.get("event") not in events:
            continue
        if set(a.get("zones") or []) & set(zones) and overlaps(a, *window) and a.get("description"):
            out.append({"kind": "text", "label": f"NWS {a['event']}, in the agency's words", "tag": "Rule",
                        "source": "alerts", "text": a["description"].replace("\n", " ").strip()})
    return out


def buoy_blocks(buoy_rows: list[dict], ids: list[str]) -> list[dict]:
    out = []
    for b in buoy_rows:
        if b["id"] in ids and b.get("status") == "ok":
            out.append({"kind": "obs", "label": f"Buoy {b['id']} {b['name']}, {b['observed']}", "tag": "Observed",
                        "source": f"buoy_{b['id']}", "text": buoy_line(b)})
    return out


def shore_blocks(site: dict, day: dict, *, mop_win: dict | None, srf_period: dict | None, buoy_rows: list[dict],
                 alerts: list[dict], window, overlaps, tz) -> list[dict]:
    blocks = []
    if mop_win:
        blocks.append({"kind": "series", "label": f"CDIP MOP waves at this entry's 10 m line ({site['mop']})", "tag": "Modeled",
                       "source": f"mop_{site['mop']}",
                       "rows": [{"time": _hour(s["t"], tz), "text": f"{s['hs_ft']} ft at {s['tp_s']} s from {s['dp_deg']}°"}
                                for s in mop_win["steps"]]})
    if day["level"] != "within":
        if srf_period:
            blocks.append({"kind": "text", "label": "NWS surf zone forecast for the county", "tag": "Rule", "source": "srf_sgx",
                           "text": f"Surf {srf_period.get('surf_text') or 'n/a'}; rip current risk {srf_period.get('rip_risk') or 'n/a'}; "
                                   f"swell from {srf_period.get('swell_from_deg') or 'n/a'}°."})
        blocks += alert_blocks(alerts, [site["land_zone"]], window, overlaps,
                               T.BEACH_EXPOSURE_EVENTS | T.BEACH_OUTSIDE_EVENTS)
        blocks += buoy_blocks(buoy_rows, [site["buoy"]])
    return blocks


def boat_blocks(rng: dict, day: dict, *, rows: list[dict], period: dict | None, synopsis: str | None,
                buoy_rows: list[dict], alerts: list[dict], window, overlaps, synopsis_source: str | None = None) -> list[dict]:
    kind = rng["kind"]
    wmax, smax = T.WIND_KT[kind][0], T.SEAS_FT[kind][0]
    blocks = []
    have = [r for r in rows if r.get("wind_kt") is not None or r.get("hs_ft") is not None]
    if have:
        def lvl(r):
            ws = T.WIND_KT[kind]
            ss = T.SEAS_FT[kind]
            levels = []
            if r.get("wind_kt") is not None:
                levels.append("within" if r["wind_kt"] <= ws[0] else "marginal" if r["wind_kt"] <= ws[1] else "outside")
            if r.get("hs_ft") is not None:
                levels.append("within" if r["hs_ft"] <= ss[0] else "marginal" if r["hs_ft"] <= ss[1] else "outside")
            return T.worst(levels)
        wins = E.windows(rows, wmax, smax)
        blocks.append({
            "kind": "series", "tag": "Modeled", "source": "model_frames",
            "label": f"Our hourly model at one point in this range ({rng['point'][0]:.2f}N {abs(rng['point'][1]):.2f}W)",
            "summary": ("Wind and seas within our thresholds " + "; ".join(f"{w['from']} to {w['to']}" for w in wins) + "."
                        if wins else "No hour with both wind and seas within our thresholds."),
            "rows": [{"time": E.hour_label(r["hour"]), "level": lvl(r),
                      "text": ", ".join(x for x in (
                          f"wind {r['wind_kt']} kt from {E.compass(r.get('wind_from_deg'))}" if r.get("wind_kt") is not None else "",
                          f"waves {r['hs_ft']} ft at {r['tp_s']} s" if r.get("hs_ft") is not None else "") if x)}
                     for r in have],
        })
        model_max = max((r["wind_kt"] for r in have if r.get("wind_kt") is not None), default=None)
        nws_max = (period or {}).get("wind", {}).get("sustained_hi_kt")
        if model_max is not None and nws_max is not None and abs(nws_max - model_max) >= 8:
            higher = "NWS" if nws_max > model_max else "our model"
            blocks.append({"kind": "note", "tag": "Modeled", "source": "model_frames",
                           "label": "NWS and our model disagree on wind",
                           "text": f"NWS forecasts up to {nws_max} kt in this zone; our model at one point peaks at {model_max} kt. "
                                   f"{higher} is higher. NWS covers the whole zone, our point does not, and the verdict "
                                   f"follows NWS."})
    if day["level"] != "within":
        if synopsis:
            blocks.append({"kind": "text", "label": "NWS forecaster's synopsis", "tag": "Rule",
                           "source": synopsis_source, "text": synopsis})
        blocks += alert_blocks(alerts, rng["alert_zones"], window, overlaps,
                               T.MARINE_OUTSIDE_EVENTS | T.MARINE_WATCH_EVENTS)
    blocks += buoy_blocks(buoy_rows, rng.get("buoys", []))
    return blocks


def synopsis_text(block: str | None) -> str | None:
    """The prose of a synopsis block: drop the zone-name and issuance lines."""
    if not block:
        return None
    lines = [ln.strip() for ln in block.strip().splitlines()]
    body = [ln for ln in lines if ln and not ln.endswith("-") and not ln[:1].isdigit()]
    text = " ".join(body)
    text = text.split("&&")[0].strip()
    i = text.lower().find("synopsis")
    if i >= 0:
        text = text[i:]
        j = text.find("...")
        if 0 <= j < 80:
            text = text[j + 3:]
    return text.strip() or None
