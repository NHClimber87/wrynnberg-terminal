"""Old fresh-start price history, aligned to the live launch.

Each file in <data>/history/*.json holds one past fresh start (see README). Points are
[hours_since_launch, price_copper, quantity_or_null]. This module puts each start on the
live clock (live launch + the same hours), builds an average line across starts, and
measures how the live floor follows that line.
"""
import glob
import json
import math
import os
from statistics import median

LIVE_LAUNCH_TS = 1793833200  # 2026-11-04 15:00 PST, WoW Forever realms open
MAX_HOURS = 60 * 24


def bucket_hours(h):
    """6-hour buckets for the first week, then 1-day buckets."""
    return 6 if h < 7 * 24 else 24


def bucket_of(h):
    size = bucket_hours(h)
    start = (h // size) * size
    return start, start + size / 2.0


class History:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "history")
        self.key = None
        self.starts = []

    def disk_key(self):
        files = sorted(glob.glob(os.path.join(self.dir, "*.json")))
        return tuple((f, os.stat(f).st_mtime_ns) for f in files)

    def load(self):
        k = self.disk_key()
        if k != self.key:
            starts = []
            for f, _ in k:
                try:
                    with open(f) as fh:
                        s = json.load(fh)
                    s["items"] = {int(i): p for i, p in (s.get("items") or {}).items()}
                    starts.append(s)
                except (OSError, ValueError, AttributeError):
                    continue
            starts.sort(key=lambda s: s.get("launch_ts") or 0)
            self.starts, self.key = starts, k
        return self.starts

    def overlay(self, iid, live_points):
        return overlay(self.load(), iid, live_points)


def start_buckets(points):
    """Median price per bucket for one start: {bucket_start: (center_hours, price)}."""
    b = {}
    for p in points:
        h, price = p[0], p[1]
        if price is None or price <= 0 or h < 0 or h > MAX_HOURS:
            continue
        s, c = bucket_of(h)
        b.setdefault(s, (c, []))[1].append(price)
    return {s: (c, median(v)) for s, (c, v) in b.items()}


def interp(line, h):
    """Linear value of line [[hours, price], ...] at h. None outside the line."""
    if not line or h < line[0][0] or h > line[-1][0]:
        return None
    for (h0, p0), (h1, p1) in zip(line, line[1:]):
        if h0 <= h <= h1:
            return p0 if h1 == h0 else p0 + (p1 - p0) * (h - h0) / (h1 - h0)
    return line[-1][1]


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def overlay(starts, iid, live_points, launch_ts=LIVE_LAUNCH_TS):
    """Old starts for one item on the live clock, the average line, and the live match."""
    per = []
    grid = {}
    for s in starts:
        pts = s["items"].get(iid)
        if not pts:
            continue
        b = start_buckets(pts)
        if not b:
            continue
        line = sorted(b.values())
        per.append({"key": s.get("key"), "label": s.get("label"), "price_kind": s.get("price_kind"),
                    "resolution": s.get("resolution"), "source": s.get("source"), "source_url": s.get("source_url"),
                    "realm": s.get("realm"), "faction": s.get("faction"),
                    "line": [[launch_ts + int(c * 3600), int(round(p))] for c, p in line]})
        for k, (c, p) in b.items():
            grid.setdefault(k, (c, []))[1].append(p)
    if not per:
        return None
    # Geometric mean across starts: each start counts the same, whatever its gold level.
    avg_h = [[c, math.exp(sum(math.log(p) for p in v) / len(v)), len(v)] for _, (c, v) in sorted(grid.items())]
    avg_line = [[c, p] for c, p, _ in avg_h]
    pairs = []
    for p in live_points:
        f = p.get("floor")
        if not f or f <= 0:
            continue
        h = (p["ts"] - launch_ts) / 3600.0
        o = interp(avg_line, h)
        if o:
            pairs.append((h, f, o))
    match = {"n": len(pairs)}
    if pairs:
        ratio = median(f / o for _, f, o in pairs)
        match["ratio"] = round(ratio, 3)
        if len(pairs) >= 4:
            r = pearson([math.log(f) for _, f, _ in pairs], [math.log(o) for _, _, o in pairs])
            match["r"] = None if r is None else round(r, 3)
        # Direction over the overlap: first third vs last third of the pairs.
        if len(pairs) >= 3:
            k = max(1, len(pairs) // 3)
            lf = median(f for _, f, _ in pairs[-k:]) / median(f for _, f, _ in pairs[:k])
            lo = median(o for _, _, o in pairs[-k:]) / median(o for _, _, o in pairs[:k])
            match["live_change"] = round(lf - 1, 3)
            match["old_change"] = round(lo - 1, 3)
            match["same_direction"] = (lf - 1) * (lo - 1) > 0 or (abs(lf - 1) < 0.05 and abs(lo - 1) < 0.05)
    scale = match.get("ratio") or 1.0
    return {"launch_ts": launch_ts, "starts": per,
            "avg": [[launch_ts + int(c * 3600), int(round(p)), n] for c, p, n in avg_h],
            "avg_scaled": [[launch_ts + int(c * 3600), int(round(p * scale))] for c, p, _ in avg_h],
            "match": match}
