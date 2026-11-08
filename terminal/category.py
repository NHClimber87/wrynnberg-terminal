"""Item categories: one price index for a group of items, such as leveling 2H Strength weapons.

A category file (<data>/categories/<key>.json, made by terminal.make_category) lists item ids.
Scan variants (AHResearch 1.1.1+) add items that the file does not know, for example new
Forever items: weapon class, the same subclasses, a two-hand slot and the required level range.

The index is chain-linked. At each scan it moves by the trimmed mean log change (top and bottom
10% dropped) of the floor of the items that the scan and the last known floors share. A new or gone item does not move it.
It starts at the median floor of the first scan, so it reads as copper.
"""
import glob
import json
import math
import os
from statistics import median

WEAPON = 2
TWO_HAND_SLOT = "INVTYPE_2HWEAPON"
SUBCLASS = {1: "2H axe", 5: "2H mace", 6: "polearm", 8: "2H sword"}
MIN_COMMON = 3      # shared items needed to move the index
TRIM = 0.1          # share of changes dropped at each end before the mean
PARTIAL = 0.5       # a scan with fewer items than this share of the scan before it is a search scan


def load(data_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(data_dir, "categories", "*.json"))):
        try:
            with open(f) as fh:
                c = json.load(fh)
            c["items"] = {int(k): v for k, v in (c.get("items") or {}).items()}
            out.append(c)
        except (OSError, ValueError, AttributeError):
            continue
    return out


def scan_members(cat, live_scans):
    """Items from scan variants that fit the category: {item_id: info}."""
    lo, hi = cat.get("req") or (0, 999)
    add = {}
    for s in live_scans:
        for v in (s.get("variants") or {}).values():
            iid = v.get("item_id")
            if iid in cat["items"] or iid in add:
                continue
            if (v.get("class_id") == WEAPON and v.get("subclass_id") in SUBCLASS
                    and v.get("equip_loc") == TWO_HAND_SLOT and lo <= (v.get("required_level") or -1) <= hi):
                add[iid] = {"type": SUBCLASS[v["subclass_id"]], "req": v["required_level"],
                            "ilvl": v.get("item_level"), "quality": v.get("quality"), "dps": None,
                            "stats": {}, "name": None, "source": None, "from_scan": True}
    return add


def trimmed_mean(xs, trim=TRIM):
    xs = sorted(xs)
    k = int(len(xs) * trim)
    xs = xs[k:len(xs) - k] or xs
    return sum(xs) / len(xs)


def index(members, live_scans):
    """Chain-linked index points for a set of item ids."""
    pts, last, idx, prev_n = [], {}, None, None
    for s in live_scans:
        n = len(s["items"])
        if prev_n and n < prev_n * PARTIAL:
            continue
        prev_n = n
        basket = {i: s["items"][i] for i in members if i in s["items"] and (s["items"][i].get("min_buyout") or 0) > 0}
        if not basket:
            continue
        floors = {i: o["min_buyout"] for i, o in basket.items()}
        if idx is None:
            idx = float(median(floors.values()))
        else:
            common = [math.log(f / last[i]) for i, f in floors.items() if i in last]
            if len(common) >= MIN_COMMON:
                idx *= math.exp(trimmed_mean(common))
        last.update(floors)
        pts.append({"ts": s["timestamp"], "floor": int(round(idx)),
                    "qty": sum(o.get("quantity_available") or 0 for o in basket.values()),
                    "floor_qty": len(basket), "market": int(median(floors.values())),
                    "listings": sum(o.get("listing_count") or 0 for o in basket.values())})
    return pts


def _at(points, ts):
    """The newest point at or before ts."""
    best = None
    for p in points:
        if p["ts"] <= ts:
            best = p
    return best


def _chg(new, old):
    return round((new / old - 1) * 100, 1) if new and old else None


def board(cat, live_scans, names, trade_by_id):
    """Rows and index series for one category."""
    members = dict(cat["items"])
    members.update(scan_members(cat, live_scans))
    seen = {i for s in live_scans for i in s["items"] if i in members}
    latest = live_scans[-1] if live_scans else None
    now = latest["timestamp"] if latest else 0
    groups = [{"id": cat["id"], "label": "Index: " + cat["label"], "members": seen}]
    for b in cat.get("bands") or []:
        lo, hi = b["req"]
        groups.append({"id": b["id"], "label": "Index: " + b["label"],
                       "members": {i for i in seen if lo <= (members[i].get("req") or -1) <= hi}})
    series, rows = {}, []
    for g in groups:
        pts = index(g["members"], live_scans)
        if not pts:
            continue
        base = _at(pts, now - 24 * 3600) or pts[0]
        trades = sorted((t for i in g["members"] for t in trade_by_id.get(i, [])), key=lambda t: t["ts"])
        series[g["id"]] = {"item_id": g["id"], "name": g["label"], "points": pts, "trades": trades,
                           "vendor_sell": None, "beta_floor_median": None, "category": cat["key"],
                           "scan_times": [s["timestamp"] for s in live_scans]}
        last = pts[-1]
        rows.append({"item_id": g["id"], "name": g["label"], "is_index": True, "type": "index",
                     "req": None, "dps": None, "source": f"{last['floor_qty']} items listed",
                     "floor": last["floor"], "qty": last["qty"], "base_floor": base["floor"], "base_qty": base["qty"],
                     "floor_chg24_pct": _chg(last["floor"], base["floor"]), "qty_chg24": last["qty"] - base["qty"],
                     "spark": [{"ts": p["ts"], "floor": p["floor"]} for p in pts]})
    for i in sorted(seen):
        m = members[i]
        pts = [{"ts": s["timestamp"], "floor": s["items"][i].get("min_buyout"), "qty": s["items"][i].get("quantity_available")}
               for s in live_scans if i in s["items"]]
        cur = latest["items"].get(i) if latest else None
        base = _at(pts, now - 24 * 3600) or pts[0]
        rows.append({"item_id": i, "name": names.get(i) or m.get("name"), "is_index": False, "type": m["type"],
                     "req": m.get("req"), "dps": m.get("dps"), "quality": m.get("quality"), "source": m.get("source"),
                     "floor": cur.get("min_buyout") if cur else None, "qty": (cur.get("quantity_available") or 0) if cur else 0,
                     "base_floor": base["floor"], "base_qty": base["qty"],
                     "floor_chg24_pct": _chg(cur.get("min_buyout"), base["floor"]) if cur else None,
                     "qty_chg24": ((cur.get("quantity_available") or 0) if cur else 0) - (base["qty"] or 0),
                     "last_seen_ts": pts[-1]["ts"], "spark": [{"ts": p["ts"], "floor": p["floor"]} for p in pts]})
    return {"key": cat["key"], "label": cat["label"], "members": len(members), "seen": len(seen),
            "source": cat.get("source"), "rows": rows}, series
