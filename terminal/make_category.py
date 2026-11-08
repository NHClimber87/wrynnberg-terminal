"""Write an item category file for the terminal from the Polestar item tables.

    python3 -m terminal.make_category --polestar <Polestar_ItemDB folder>

The category "2h-str-leveling" holds two-hand axes, maces, swords and polearms with
required level 15-32 that are not caster weapons. Items.lua (Classic Era client tables)
gives the stats. Forever.lua (from ForeverLevelingBiS, MIT) overlays Forever stats, names and
the dungeon or quest source of leveling items. The output goes to
<data>/categories/<key>.json. The terminal reads it at the next data build.
"""
import argparse
import json
import os
import re

from terminal import config

ROW = re.compile(r'^\[(\d+)\]="([^"]*)",?$')
TWO_HAND = 17
WEAPON = 2
SUBCLASS = {1: "2H axe", 5: "2H mace", 6: "polearm", 8: "2H sword"}
CATEGORIES = {
    "2h-str-leveling": {"id": -1, "label": "2H Str weapons, req 15-32", "req": (15, 32),
                        "bands": [(-2, "req 15-21", 15, 21), (-3, "req 22-26", 22, 26), (-4, "req 27-32", 27, 32)]},
}


def sections(path):
    """{section name: {id: value}} for one generated Lua file. The first table is "items"."""
    out, cur = {}, "items"
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            m = re.match(r"^(\w+) = \{$", line)
            if m:
                cur = m.group(1)
                continue
            m = ROW.match(line)
            if m:
                out.setdefault(cur, {})[int(m.group(1))] = m.group(2)
    return out


def parse_item(s):
    f = s.split()
    r = {"inv": int(f[0]), "ilvl": int(f[1]), "req": int(f[2]), "quality": int(f[3]),
         "class": int(f[4]), "subclass": int(f[5]), "stats": {}}
    for tok in f[6:]:
        m = re.match(r"^([a-z]+)(-?\d+)$", tok)
        if m:
            r["stats"][m.group(1)] = int(m.group(2))
    st = r["stats"]
    if st.get("speed"):
        r["dps"] = round((st.get("dmin", 0) + st.get("dmax", 0)) / 2 / (st["speed"] / 1000), 1)
    return r


def source_text(src, lv):
    parts = []
    for tok in (src or "").split():
        if tok[0] == "q":
            q = (lv.get("quests") or {}).get(int(tok[1:]), "")
            name = q.split("|")[-1] if q else tok
            parts.append(f"quest: {name}")
        elif tok[0] == "d":
            m = re.match(r"d(\d+)x(\d+)z(\d+)", tok)
            if m:
                npc = (lv.get("npcs") or {}).get(int(m.group(1))) if m.group(1) != "0" else "trash"
                zone = (lv.get("zones") or {}).get(int(m.group(3)), "zone " + m.group(3))
                parts.append(f"drop: {npc or 'npc ' + m.group(1)} ({zone}, {int(m.group(2)) / 10:g}%)")
        elif tok[0] == "c":
            parts.append("crafted")
    return "; ".join(parts) or None


def build(polestar, key):
    cat = CATEGORIES[key]
    era = sections(os.path.join(polestar, "Items.lua"))["items"]
    lv = sections(os.path.join(polestar, "Forever.lua"))
    stats = dict(era)
    stats.update(lv.get("items") or {})
    lo, hi = cat["req"]
    items = {}
    for iid, s in stats.items():
        r = parse_item(s)
        if r["inv"] != TWO_HAND or r["class"] != WEAPON or r["subclass"] not in SUBCLASS:
            continue
        if not lo <= r["req"] <= hi:
            continue
        st = r["stats"]
        phys = st.get("str", 0) + st.get("agi", 0)
        caster = st.get("int", 0) + st.get("spi", 0) + st.get("sp", 0) + st.get("heal", 0)
        if phys == 0 and caster > 0:
            continue
        items[str(iid)] = {"type": SUBCLASS[r["subclass"]], "req": r["req"], "ilvl": r["ilvl"],
                           "quality": r["quality"], "dps": r.get("dps"),
                           "stats": {k: v for k, v in st.items() if k not in ("dmin", "dmax", "speed")},
                           "name": (lv.get("names") or {}).get(iid),
                           "source": source_text((lv.get("src") or {}).get(iid), lv),
                           "forever": iid in (lv.get("items") or {})}
    return {"key": key, "id": cat["id"], "label": cat["label"], "req": list(cat["req"]),
            "bands": [{"id": b[0], "label": b[1], "req": [b[2], b[3]]} for b in cat["bands"]],
            "source": "Polestar_ItemDB: Items.lua (Classic Era 1.15.9 client tables) + Forever.lua "
                      "(ForeverLevelingBiS v0.12.0-beta by Thunder, MIT)",
            "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--polestar", required=True, help="Polestar_ItemDB folder with Items.lua and Forever.lua")
    ap.add_argument("--key", default="2h-str-leveling", choices=sorted(CATEGORIES))
    ap.add_argument("--data", default=config.DATA_DIR)
    a = ap.parse_args()
    out = build(a.polestar, a.key)
    d = os.path.join(a.data, "categories")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, a.key + ".json")
    with open(p + ".tmp", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(p + ".tmp", p)
    print(f"{p}: {len(out['items'])} items")


if __name__ == "__main__":
    main()
