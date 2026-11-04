"""Every vessel the SoCal receivers hear, thinned, for the tool's find-any-MMSI view.

The roster log (live_logger.py -> live/<date>.jsonl) keeps only fleet boats at
full rate. This keeps everything else too, small enough to serve per hull:

    any/<UTC date>/<NN>.csv   NN = last two MMSI digits; rows
                              mmsi,epoch,lat,lon,sog,cog,heading
    any/vessels.json          latest fix + static identity (name, AIS type,
                              length, call sign, destination) per hull heard
                              in the last INDEX_DAYS days

Thinning: one point per MOVING_S while under way (or once the hull has
drifted MOVED_KM), a heartbeat every IDLE_S while stopped. A gap longer than
IDLE_S between two points therefore means the hull was out of range, not
sitting still. Day folders older than KEEP_DAYS are deleted. Pure stdlib so
the tests can import it without the socket client.
"""
import calendar
import json
import math
import os
import re
import shutil
import time

KEEP_DAYS = 14        # day folders kept on the fleet-any branch
INDEX_DAYS = 7        # vessels.json forgets a hull silent this long
MOVING_S = 120        # under way: one point per 2 min
IDLE_S = 1800         # stopped: a heartbeat every 30 min
MOVED_KM = 0.3        # ...or sooner once it has drifted this far
MOVING_KT = 1.0
FLUSH_S = 300

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def epoch(ts):
    """'2026-11-04T20:26:11' (UTC) -> seconds; None when unparseable."""
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def km(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def clean(v, bad=None, nd=1):
    """AIS 'not available' sentinels (sog 102.3, cog 360, heading 511) -> None."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if bad is not None and v >= bad:
        return None
    return round(v, nd)


def clean_name(s):
    s = (s or "").replace("@", " ").strip()
    return s or None


def shard(mmsi):
    return f"{int(mmsi) % 100:02d}"


class AnyLog:
    def __init__(self, root, now=None):
        self.root = root
        self.last = {}        # mmsi -> (t, lat, lon) of the last point written
        self.fh = {}          # shard -> open file for self.day
        self.day = None
        self.rows = 0
        self.flushed = now if now is not None else time.time()
        os.makedirs(root, exist_ok=True)
        self.idx = {}
        try:
            with open(os.path.join(root, "vessels.json"), encoding="utf-8") as f:
                self.idx = json.load(f)
        except (OSError, ValueError):
            pass
        self.idx.setdefault("v", {})
        if not self.idx.get("since"):
            self.idx["since"] = time.strftime("%Y-%m-%d", time.gmtime(self.flushed))

    def _entry(self, mmsi, t):
        v = self.idx["v"].setdefault(str(mmsi), {})
        v["u"] = max(v.get("u", 0), int(t))
        return v

    def position(self, mmsi, t, lat, lon, sog=None, cog=None, heading=None, name=None):
        """One position report. Returns True when it was written to a shard."""
        if t is None or lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            return False
        t = int(t)
        sog, cog, heading = clean(sog, 102.3), clean(cog, 360), clean(heading, 511, 0)
        v = self._entry(mmsi, t)
        if name:
            v["n"] = name
        if t >= v.get("t", 0):
            v.update(t=t, la=round(lat, 5), lo=round(lon, 5), s=sog, c=cog, h=heading)
        prev = self.last.get(mmsi)
        if prev:
            gap = t - prev[0]
            if gap <= 0:
                return False
            moving = sog is not None and sog >= MOVING_KT
            if not (gap >= IDLE_S or (gap >= MOVING_S and (moving or km(prev[1], prev[2], lat, lon) >= MOVED_KM))):
                return False
        self.last[mmsi] = (t, lat, lon)
        day = time.strftime("%Y-%m-%d", time.gmtime(t))
        row = ",".join([str(mmsi), str(t), f"{lat:.5f}", f"{lon:.5f}",
                        "" if sog is None else f"{sog:g}", "" if cog is None else f"{cog:g}",
                        "" if heading is None else f"{heading:g}"]) + "\n"
        if self.day is None or day > self.day:
            self._close()
            self.day = day
        if day == self.day:
            f = self.fh.get(shard(mmsi))
            if f is None:
                d = os.path.join(self.root, day)
                os.makedirs(d, exist_ok=True)
                f = self.fh[shard(mmsi)] = open(os.path.join(d, shard(mmsi) + ".csv"), "a", encoding="utf-8")
            f.write(row)
        else:  # a late report for yesterday: append and close
            d = os.path.join(self.root, day)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, shard(mmsi) + ".csv"), "a", encoding="utf-8") as f:
                f.write(row)
        self.rows += 1
        return True

    def static(self, mmsi, kind, body, t=None):
        """ShipStaticData (class A) or StaticDataReport (class B) from aisstream."""
        v = self._entry(mmsi, t if t is not None else time.time())
        body = body or {}
        name = typ = cs = dest = None
        dim = {}
        if kind == "ShipStaticData":
            name, typ, cs, dest = body.get("Name"), body.get("Type"), body.get("CallSign"), body.get("Destination")
            dim = body.get("Dimension") or {}
        else:
            a, b = body.get("ReportA") or {}, body.get("ReportB") or {}
            if a.get("Valid", True):
                name = a.get("Name")
            if b.get("Valid", True):
                typ, cs, dim = b.get("ShipType"), b.get("CallSign"), b.get("Dimension") or {}
        name, cs, dest = clean_name(name), clean_name(cs), clean_name(dest)
        if name:
            v["n"] = name
        if typ:
            v["ty"] = int(typ)
        if cs:
            v["cs"] = cs
        if dest:
            v["d"] = dest
        length = (dim.get("A") or 0) + (dim.get("B") or 0)
        if 0 < length < 500:
            v["len"] = int(length)

    def _close(self):
        for f in self.fh.values():
            f.close()
        self.fh = {}

    def flush(self, force=False, now=None):
        """Every FLUSH_S: flush shards, rewrite vessels.json, prune old days."""
        now = now if now is not None else time.time()
        if not force and now - self.flushed < FLUSH_S:
            return False
        self.flushed = now
        for f in self.fh.values():
            f.flush()
        cutoff = now - INDEX_DAYS * 86400
        self.idx["v"] = {k: x for k, x in self.idx["v"].items() if x.get("u", 0) >= cutoff}
        self.idx["updated"] = int(now)
        path = os.path.join(self.root, "vessels.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.idx, f, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        oldest = time.strftime("%Y-%m-%d", time.gmtime(now - KEEP_DAYS * 86400))
        for name in os.listdir(self.root):
            if _DAY.match(name) and name < oldest:
                shutil.rmtree(os.path.join(self.root, name), ignore_errors=True)
        return True

    def close(self):
        self.flush(force=True)
        self._close()
