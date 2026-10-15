"""Parsers for NWS / NHC text products.

Three products feed the report:
  CWF  Coastal Waters Forecast (SGX, LOX): per marine zone, per period wind,
       seas and a "Wave Detail" line that lists each wave train separately.
  OFF  NHC Offshore Waters Forecast (OFFPZ7): PMZ009 covers the border south
       to 30N within 60 nm, so it is the official forecast for the Coronados.
  SRF  Surf Zone Forecast (SGX): per coastal county, rip current risk, surf
       height with sets, thunderstorm potential, water temperature, swell
       direction.

All three share the UGC layout: blocks separated by "$$", each starting with a
zone line such as "PZZ740-152200-", optional "...HEADLINE..." lines, then
periods introduced by ".TODAY...", ".TONIGHT...", ".FRI NIGHT..." and so on.
The parsers are pure functions over text so they can be tested on saved
products (pipeline/tests/fixtures/reports/).
"""
from __future__ import annotations

import re

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
COMPASS_DEG = {d: i * 22.5 for i, d in enumerate(COMPASS)}
_DIR = r"(?:NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|SE|SW|NW|N|E|S|W)"

_ZONE_LINE = re.compile(r"^((?:[A-Z]{2}[ZC]\d{3})(?:[->](?:[A-Z]{2}[ZC])?\d{3})*)-\d{6}-\s*$", re.M)
_PERIOD = re.compile(r"^\.([A-Z][A-Z ]*?)\.\.\.", re.M)


def _expand_ugc(line: str) -> list[str]:
    """'PZZ750-775' -> ['PZZ750', 'PZZ775']; ranges with '>' expand inclusive."""
    codes: list[str] = []
    prefix = None
    tokens = re.split(r"([->])", line)
    last_num = None
    pending_range = False
    for tok in tokens:
        if tok in ("-", ""):
            continue
        if tok == ">":
            pending_range = True
            continue
        m = re.fullmatch(r"([A-Z]{2}[ZC])?(\d{3})", tok)
        if not m:
            continue
        if m.group(1):
            prefix = m.group(1)
        num = int(m.group(2))
        if pending_range and last_num is not None:
            codes.extend(f"{prefix}{n:03d}" for n in range(last_num + 1, num + 1))
            pending_range = False
        else:
            codes.append(f"{prefix}{num:03d}")
        last_num = num
    return codes


def split_zones(text: str) -> dict[str, str]:
    """Map every zone code to the text block that forecasts it."""
    out: dict[str, str] = {}
    for block in text.replace("\r", "").split("$$"):
        m = _ZONE_LINE.search(block)
        if not m:
            continue
        body = block[m.end():]
        for code in _expand_ugc(m.group(1)):
            out[code] = body
    return out


def _join(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def headlines(block: str) -> list[str]:
    """'...SMALL CRAFT ADVISORY IN EFFECT THROUGH LATE TONIGHT...' lines that
    precede the first forecast period, joined across line breaks."""
    first = _PERIOD.search(block)
    head = block[: first.start()] if first else block
    joined = _join(head)
    found = re.findall(r"\.\.\.([A-Z0-9][A-Z0-9 ,/'\-]+?)\.\.\.", joined)
    return [h.strip() for h in found if len(h.strip()) > 6]


def periods(block: str) -> list[tuple[str, str]]:
    """[(name, joined text)] in order, e.g. ('TODAY', 'Wind N 10 kt...')."""
    ms = list(_PERIOD.finditer(block))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(block)
        body = block[m.end():end]
        # Stop at the SRF legend / product trailer.
        body = body.split("&&")[0]
        out.append((m.group(1).strip(), _join(body)))
    return out


def _num(s: str | None) -> int | None:
    return int(s) if s is not None else None


def parse_wind(wind_text: str) -> dict:
    """Wind clause -> max sustained (hi), gusts, directions mentioned.

    Conservative on purpose: LOX splits a period into sub-areas ("From Point
    Mugu to Santa Monica..., NE wind 20 to 30 kt. Otherwise, NW wind 10 kt"),
    and a boat crossing the zone can meet any of them, so we keep the maximum.
    """
    t = wind_text
    gusts = [int(g) for g in re.findall(r"[Gg]usts?\s+(?:to\s+|up\s+to\s+)?(\d+)\s*kt", t)]
    t_ng = re.sub(r"[Gg]usts?\s+(?:to\s+|up\s+to\s+)?\d+\s*kt", " ", t)
    his: list[int] = []
    los: list[int] = []
    for lo, hi in re.findall(r"(\d+)(?:\s+to\s+(\d+))?\s*kt", t_ng):
        lo_i = int(lo)
        hi_i = int(hi) if hi else lo_i
        his.append(hi_i)
        los.append(lo_i)
    if re.search(r"less than (\d+)\s*kt", t_ng):
        # "variable less than 10 kt" -> the 10 is an upper bound, not a speed.
        lt = [int(x) for x in re.findall(r"less than (\d+)\s*kt", t_ng)]
        for x in lt:
            if x in his:
                his.remove(x)
                his.append(x - 1)
    dirs = re.findall(rf"\b({_DIR})(?:\s+to\s+({_DIR}))?\s+winds?\b", t)
    dirs += re.findall(rf"\b[Ww]inds?\s+({_DIR})(?:\s+to\s+({_DIR}))?\b", t)
    dirs += re.findall(rf"\bbecoming\s+({_DIR})(?:\s+to\s+({_DIR}))?\b", t)
    flat: list[str] = []
    for pair in dirs:
        for d in pair:
            if d and d not in flat:
                flat.append(d)
    return {
        "sustained_hi_kt": max(his) if his else None,
        "sustained_lo_kt": min(los) if los else None,
        "gust_kt": max(gusts) if gusts else None,
        "directions": flat,
        "variable": bool(re.search(r"\bvariable\b", t, re.I)),
    }


def parse_marine_period(text: str) -> dict:
    """One CWF/OFF period -> wind, seas, wave trains, period, fog."""
    seas_at = re.search(r"\bSeas\b", text)
    wind_text = text[: seas_at.start()] if seas_at else text
    rest = text[seas_at.start():] if seas_at else ""
    seas = re.search(r"Seas\s+(?:around\s+)?(\d+)(?:\s+to\s+(\d+))?\s*(?:ft|feet|foot)", rest)
    seas_lo = _num(seas.group(1)) if seas else None
    seas_hi = (_num(seas.group(2)) or seas_lo) if seas else None
    trains = []
    detail = re.search(r"Wave Detail:(.*?)(?:\.\s|$)", rest)
    if detail:
        for d, ft, s in re.findall(rf"\b({_DIR})\s+(\d+)\s+(?:ft|feet|foot)\s+at\s+(\d+)\s+seconds", detail.group(1)):
            trains.append({"dir": d, "ft": int(ft), "s": int(s)})
    per = re.search(r"Period\s+(\d+)\s+seconds", rest)
    fog_nm = None
    fog = re.search(r"[Vv]isibility\s+(\d+(?:\.\d+)?|1/2|1/4)\s+(?:NM|nm)\s+or\s+less", text)
    if fog:
        v = fog.group(1)
        fog_nm = {"1/2": 0.5, "1/4": 0.25}.get(v, None) or float(v)
    dense_fog = bool(re.search(r"dense fog", text, re.I))
    return {
        "wind": parse_wind(wind_text),
        "seas_lo_ft": seas_lo,
        "seas_hi_ft": seas_hi,
        "trains": trains,
        "period_s": int(per.group(1)) if per else None,
        "fog_vis_nm": fog_nm,
        "dense_fog": dense_fog,
        "text": text,
    }


def parse_marine_zone(block: str) -> dict:
    return {
        "headlines": headlines(block),
        "periods": [{"name": n, **parse_marine_period(t)} for n, t in periods(block)],
    }


_SRF_FIELD = re.compile(r"([A-Z][A-Za-z ]+?)\*?\.{3,}\s*(.*?)(?=\s[A-Z][A-Za-z ]+?\*?\.{3,}|$)")


def parse_srf_period(text: str) -> dict:
    fields: dict[str, str] = {}
    for key, val in _SRF_FIELD.findall(text):
        fields[key.strip().lower()] = val.strip()
    rip = fields.get("rip current risk", "")
    rip_level = None
    for lvl in ("Low", "Moderate", "High"):
        if rip.lower().startswith(lvl.lower()):
            rip_level = lvl
    surf_txt = fields.get("surf height", "")
    surf = re.search(r"(\d+)(?:\s+to\s+(\d+))?\s+(?:feet|foot|ft)", surf_txt)
    sets = re.search(r"[Ss]ets\s+(?:to\s+|up\s+to\s+)?(\d+)\s+(?:feet|foot|ft)", surf_txt)
    surf_lo = _num(surf.group(1)) if surf else None
    surf_hi = (_num(surf.group(2)) or surf_lo) if surf else None
    wt = re.search(r"(\d+)(?:\s+to\s+(\d+))?\s+degrees", fields.get("water temperature", ""))
    swell = re.search(r"[Ss]well from\s+(\d+)\s+degrees", fields.get("remarks", "") or text)
    thunder = fields.get("thunderstorm potential", "").rstrip(".") or None
    return {
        "rip_risk": rip_level,
        "surf_lo_ft": surf_lo,
        "surf_hi_ft": surf_hi,
        "sets_ft": _num(sets.group(1)) if sets else surf_hi,
        "surf_text": re.sub(r"\.\s+Sets", ", sets", surf_txt).rstrip(".") or None,
        "thunder": thunder,
        "water_temp_lo_f": _num(wt.group(1)) if wt else None,
        "water_temp_hi_f": (_num(wt.group(2)) or _num(wt.group(1))) if wt else None,
        "swell_from_deg": _num(swell.group(1)) if swell else None,
    }


def parse_srf_zone(block: str) -> dict:
    return {
        "headlines": headlines(block),
        "periods": [{"name": n, **parse_srf_period(t)} for n, t in periods(block)],
    }


def issued_line(text: str) -> str | None:
    """The human issuance line, e.g. '201 AM PDT Thu Oct 15 2026'."""
    m = re.search(r"^\d{3,4} [AP]M [A-Z]{3} \w{3} \w{3} \d{1,2} \d{4}$", text, re.M)
    return m.group(0) if m else None


_DAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_DAY_FULL = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_TODAY_NAMES = ("TODAY", "REST OF TODAY", "THIS AFTERNOON", "THIS MORNING")


def pick_day_period(period_list: list[dict], day_offset: int, weekday: int) -> dict | None:
    """The daytime period for today (offset 0) or a later day.

    `weekday` is the target day's Monday=0 index. Today's period may be named
    TODAY / REST OF TODAY / THIS AFTERNOON; later days use the weekday name
    (FRI in CWF/OFF, FRIDAY in SRF). Night periods are never picked.
    """
    for p in period_list:
        name = p["name"].upper()
        if "NIGHT" in name or name in ("TONIGHT", "OVERNIGHT", "THIS EVENING"):
            continue
        if day_offset == 0 and name in _TODAY_NAMES:
            return p
        if day_offset > 0 and name in (_DAY_ABBR[weekday], _DAY_FULL[weekday]):
            return p
    return None
