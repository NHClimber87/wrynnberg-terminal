"""Build the Wrynnberg Terminal dataset from SavedVariables.

Two layers:
  extract_account(parsed)  -> compact dict for one account (cached on disk per file copy)
  build(extracts, ...)     -> the dataset the page shows (pure, tested)

Rules that the tests hold:
  * Live = realm "CAMWOW US2 Realm 11 RP", Alliance. Beta = "Classic Beta PvE 2", Alliance,
    October 10 2026 or later. The two never merge. Every beta value sits under a key that
    starts with "beta_" and never enters a live total, average or margin.
  * Each account holds a full copy of the AHResearch scans: scans are unique by timestamp,
    and scans with identical item data are one scan (the add-on replicates scans).
  * Midas trade rows: an account can hold a replicated copy of another account's rows.
    Rows merge as a multiset union (the highest count of each identical row in any one account).
  * Quantity listed is supply. Only Midas "sold" and "returned" rows are sales evidence.
  * Money is integer copper.
"""
import datetime as dt
import hashlib
import json
import os
import pickle
import re
import statistics
import string
import sys
from collections import Counter, defaultdict

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from terminal import config  # noqa: E402
from terminal import category  # noqa: E402

RECIPE = re.compile(r"^(Recipe|Pattern|Formula|Plans|Schematic|Manual|Design):")
LIVE_TRADE_REALM = f"{config.LIVE_REALM} {config.FACTION}"
BETA_TRADE_REALM = f"{config.BETA_REALM} {config.FACTION}"
LIVE_SCOPE = f"|{config.LIVE_REALM}|{config.FACTION}"
BETA_SCOPE = f"|{config.BETA_REALM}|{config.FACTION}"

# Owner's nine characters; any other live character is an AH or bank alt.
MAINS = config.MAINS

# Classic recipes, NOT checked in Forever. Shown as "classic, unverified".
CLASSIC_CRAFTS = {
    "Alchemy": {
        "Minor Healing Potion": (1, [("Peacebloom", 1), ("Silverleaf", 1), ("Empty Vial", 1)]),
        "Minor Mana Potion": (25, [("Mageroyal", 1), ("Silverleaf", 1), ("Empty Vial", 1)]),
        "Elixir of Minor Agility": (50, [("Swiftthistle", 1), ("Silverleaf", 1), ("Empty Vial", 1)]),
        "Lesser Healing Potion": (55, [("Silverleaf", 1), ("Briarthorn", 1), ("Empty Vial", 1)]),
        "Swiftness Potion": (60, [("Swiftthistle", 1), ("Briarthorn", 1), ("Empty Vial", 1)]),
        "Healing Potion": (110, [("Bruiseweed", 1), ("Briarthorn", 1), ("Leaded Vial", 1)]),
        "Lesser Mana Potion": (120, [("Mageroyal", 1), ("Stranglekelp", 1), ("Empty Vial", 1)]),
    },
    "Tailoring": {
        "Bolt of Linen Cloth": (1, [("Linen Cloth", 2)]),
        "Heavy Linen Gloves": (35, [("Linen Cloth", 4), ("Coarse Thread", 1)]),
        "Linen Bag": (45, [("Linen Cloth", 6), ("Coarse Thread", 3)]),
        "Bolt of Woolen Cloth": (75, [("Wool Cloth", 3)]),
    },
}
CRAFT_PROFESSIONS = ("Alchemy", "Tailoring", "Enchanting")
HERBS = ["Peacebloom", "Silverleaf", "Earthroot", "Mageroyal", "Briarthorn", "Bruiseweed",
         "Swiftthistle", "Stranglekelp", "Wild Steelbloom", "Grave Moss", "Kingsblood", "Liferoot",
         "Fadeleaf", "Goldthorn", "Khadgar's Whisker", "Wintersbite", "Firebloom", "Purple Lotus",
         "Sungrass", "Ghost Mushroom"]
# Bump when extract_account keeps new fields, so old cache files rebuild.
EXTRACT_VERSION = 4
# Random-suffix gear: a green (quality 2) weapon (class 2) or armor (class 4) can carry "of the Whale" etc.
SUFFIX_CLASSES = (2, 4)
ENCHANT_MATS = ["Strange Dust", "Lesser Magic Essence", "Greater Magic Essence", "Small Glimmering Shard"]


def _read_json(path):
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------- extract (per account)

def _realm_of_scope(scope):
    if LIVE_SCOPE in scope or scope.endswith(LIVE_SCOPE):
        return "live"
    if BETA_SCOPE in scope or scope.endswith(BETA_SCOPE):
        return "beta"
    return None


def extract_account(ahr, midas, alts):
    """Keep only what the terminal uses. Inputs are the parsed *DB tables (dicts)."""
    ahr, midas, alts = ahr or {}, midas or {}, alts or {}
    scans = []
    for s in ahr.get("scans") or []:
        if s.get("faction") != config.FACTION or not s.get("complete"):
            continue
        if s.get("realm") == config.LIVE_REALM:
            kind = "live"
        elif s.get("realm") == config.BETA_REALM and s.get("timestamp", 0) >= config.BETA_LATE_START:
            kind = "beta"
        else:
            continue
        items = {}
        for iid, o in (s.get("items") or {}).items():
            items[int(iid)] = {k: o.get(k) for k in (
                "item_name", "min_buyout", "min_price_quantity", "quantity_available",
                "listing_count", "market_price", "vendor_sell_price", "class_id", "quality")}
        variants = {}
        for key, v in (s.get("variants") or {}).items():
            if not isinstance(v, dict) or v.get("item_id") is None:
                continue
            variants[str(key)] = {k: v.get(k) for k in (
                "item_id", "suffix_id", "item_name", "min_buyout", "min_price_quantity",
                "quantity_available", "listing_count", "market_price")}
            variants[str(key)]["item_id"] = int(v["item_id"])
        scans.append({"kind": kind, "timestamp": int(s["timestamp"]), "items": items, "variants": variants})
    # Suffix gear that AHResearch 1.1.1+ saw in bags, bank, mail and own auctions, keyed "itemID:suffix".
    owned = {}
    for key, c in (ahr.get("owned") or {}).items():
        realm, _, char = str(key).partition("|")
        if realm != config.LIVE_REALM or (c or {}).get("faction") != config.FACTION:
            continue
        owned[char] = {}
        for where in ("bags", "bank", "mail", "auctions"):
            w = c.get(where)
            if isinstance(w, dict):
                owned[char][where] = {"ts": w.get("ts"), "items": {
                    str(k): {f: e.get(f) for f in ("item_id", "suffix_id", "item_name", "count", "buyout")}
                    for k, e in (w.get("items") or {}).items() if isinstance(e, dict)}}
    offers = {}
    for key, o in ((ahr.get("vendorIntel") or {}).get("offers") or {}).items():
        kind = _realm_of_scope(o.get("scope") or "")
        if kind:
            md = o.get("metadata") or {}
            offers[o.get("offer_id") or key] = {
                "kind": kind, "item_id": o.get("item_id"), "name": md.get("item_name"),
                "tradable": md.get("tradable"), "vendor": o.get("vendor_name"),
                "location": o.get("vendor_location"), "cost": o.get("vendor_buy_price"),
                "stock_type": o.get("stock_type"), "stock_limit": o.get("vendor_stock_limit"),
                "timestamp": o.get("timestamp")}
    trade = [e for e in ((midas.get("trade") or {}).get("log") or [])
             if e.get("realm") in (LIVE_TRADE_REALM, BETA_TRADE_REALM)]
    chars = {}
    for key, c in (midas.get("chars") or {}).items():
        if c.get("scope") == LIVE_TRADE_REALM:
            chars[key.split("-")[0]] = {k: c.get(k) for k in (
                "money", "bags", "bank", "mail", "auctions", "bagsTs", "bankTs", "mailTs", "auctionsTs")}
    alt_chars = {}
    for full, c in (alts.get("chars") or {}).items():
        if (c.get("realm") or "").replace(" ", "") == config.LIVE_REALM.replace(" ", ""):
            alt_chars[full.split(" ")[0]] = {k: c.get(k) for k in (
                "class", "level", "profs", "profMax", "money", "updated", "bank", "bankAt")}
    return {
        "scans": scans, "offers": offers, "trade": trade, "chars": chars,
        "watch": sorted(int(k) for k, v in (midas.get("watch") or {}).items() if v),
        "alts": alt_chars, "recipe_info": alts.get("recipeInfo") or {}, "owned": owned,
        # Midas name -> id (lower case). A last fallback for items that no scan names.
        "midas_names": {str(k): int(v) for k, v in (midas.get("nameIndex") or {}).items() if str(v).isdigit()},
    }


def load_account(acct_dir, cache_dir, loader=None):
    """Parse one account folder once per copy. The cache key is the size and mtime of each file."""
    if loader is None:
        from terminal import savedvars
        loader = savedvars.load
    meta, key = {}, []
    for f in config.FILES:
        p = os.path.join(acct_dir, f + ".lua")
        if os.path.exists(p):
            st = os.stat(p)
            meta[f] = int(st.st_mtime)
            key.append(f"{f}:{st.st_size}:{st.st_mtime_ns}:v{EXTRACT_VERSION}")
        else:
            meta[f] = None
    digest = hashlib.sha1("|".join(key).encode()).hexdigest()[:16]
    os.makedirs(cache_dir, exist_ok=True)
    cpath = os.path.join(cache_dir, f"{os.path.basename(acct_dir)}-{digest}.pickle")
    if os.path.exists(cpath):
        with open(cpath, "rb") as fh:
            ex = pickle.load(fh)
    else:
        def db(f, name):
            p = os.path.join(acct_dir, f + ".lua")
            return loader(p).get(name) if os.path.exists(p) else None
        ex = extract_account(db("AHResearch", "AHResearchDB"), db("Midas", "MidasDB"), db("AltsForever", "AltsForeverDB"))
        with open(cpath + ".tmp", "wb") as fh:
            pickle.dump(ex, fh)
        os.replace(cpath + ".tmp", cpath)
        for old in os.listdir(cache_dir):
            if old.startswith(os.path.basename(acct_dir) + "-") and old != os.path.basename(cpath):
                os.remove(os.path.join(cache_dir, old))
    ex["file_mtime"] = meta
    return ex


# ---------------------------------------------------------------- merge helpers

def merge_scans(extracts, kind):
    """Unique scans of one kind, oldest first. Same timestamp or same item data = one scan."""
    by_ts = {}
    for ex in extracts:
        for s in ex["scans"]:
            if s["kind"] == kind:
                by_ts.setdefault(s["timestamp"], s)
    out, seen = [], set()
    for ts in sorted(by_ts):
        h = hashlib.sha1(json.dumps(sorted(by_ts[ts]["items"].items()), sort_keys=True, default=str).encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append(by_ts[ts])
    return out


def _row_key(e):
    return tuple(sorted((k, json.dumps(v, sort_keys=True)) for k, v in e.items()))


def merge_trade(extracts):
    """Multiset union: an identical row counts as often as the account that holds it most."""
    best = Counter()
    rows = {}
    for ex in extracts:
        c = Counter()
        for e in ex["trade"]:
            k = _row_key(e)
            c[k] += 1
            rows[k] = e
        for k, n in c.items():
            best[k] = max(best[k], n)
    out = []
    for k, n in best.items():
        out.extend([rows[k]] * n)
    out.sort(key=lambda e: (e.get("ts") or 0, e.get("name") or ""))
    return out


def _char_ts(c):
    return max([c.get(k) or 0 for k in ("bagsTs", "bankTs", "mailTs", "auctionsTs")])


def merge_chars(extracts):
    out = {}
    for ex in extracts:
        for name, c in ex["chars"].items():
            if name not in out or _char_ts(c) > _char_ts(out[name]):
                out[name] = c
    return out


def merge_alts(extracts):
    out = {}
    for ex in extracts:
        for name, c in ex["alts"].items():
            if name not in out or (c.get("updated") or 0) > (out[name].get("updated") or 0):
                out[name] = dict(c, account=ex.get("account"))
    return out


# ---------------------------------------------------------------- panels

def fill_item_meta(live_scans, beta_scans, trade, extracts):
    """Scans store item_name "" and quality -1 when the game had not loaded the item yet.
    Fill those fields from the newest record that has them, so an empty name never hides a known one.
    Names: live scans, then trade log, beta scans, Midas nameIndex. Class, quality and vendor price:
    live scans only (no beta value enters a live panel). Returns new scan dicts; the inputs stay unchanged."""
    meta = {}
    for s in live_scans:
        for iid, o in s["items"].items():
            m = meta.setdefault(iid, {})
            if o.get("item_name"):
                m["item_name"] = o["item_name"]
            for k in ("class_id", "vendor_sell_price"):
                if o.get(k) is not None:
                    m[k] = o[k]
            if (o.get("quality") or -1) >= 0:
                m["quality"] = o["quality"]
    fallback = {}
    for ex in extracts:
        for name, iid in (ex.get("midas_names") or {}).items():
            fallback.setdefault(iid, re.sub(r"(?<= )(Of|The|A|And)(?= )", lambda m: m.group(1).lower(), string.capwords(name, " ")))
    for s in beta_scans:
        for iid, o in s["items"].items():
            if o.get("item_name"):
                fallback[iid] = o["item_name"]
    for e in trade:
        if e.get("id") and e.get("name"):
            fallback[e["id"]] = e["name"]
    out = []
    for s in live_scans:
        items, changed = {}, False
        for iid, o in s["items"].items():
            m = meta.get(iid, {})
            fix = {}
            if not o.get("item_name"):
                fix["item_name"] = m.get("item_name") or fallback.get(iid)
            for k in ("class_id", "vendor_sell_price"):
                if o.get(k) is None and m.get(k) is not None:
                    fix[k] = m[k]
            if (o.get("quality") is None or o["quality"] < 0) and "quality" in m:
                fix["quality"] = m["quality"]
            if fix:
                changed = True
                o = dict(o, **fix)
            items[iid] = o
        out.append(dict(s, items=items) if changed else s)
    return out


def merge_owned(extracts):
    """Owned suffix gear per character: for each place, the newest copy across accounts."""
    out = {}
    for ex in extracts:
        for char, places in (ex.get("owned") or {}).items():
            c = out.setdefault(char, {})
            for where, w in places.items():
                if (w.get("ts") or 0) >= ((c.get(where) or {}).get("ts") or 0):
                    c[where] = w
    return out


def suffix_index(live_scans, trade, items):
    """Items whose AH floor mixes random suffixes ("of the Whale", "of Power").

    status "seen": AHResearch recorded suffix variants, or a Midas trade row names a suffix.
    status "possible": a green weapon or armor with no evidence yet. Check the suffix by hand.
    variants: the floor of each suffix in the newest live scan with variant data for the item.
    """
    base = {iid: o.get("item_name") or "" for iid, (o, _) in items.items()}
    out = {}
    for s in live_scans:
        by = defaultdict(list)
        for key, v in (s.get("variants") or {}).items():
            by[v["item_id"]].append((key, v))
        for iid, vs in by.items():
            b = base.get(iid, "")
            rows = []
            for key, v in vs:
                n = v.get("item_name") or ""
                rows.append({"key": key, "suffix_id": v.get("suffix_id"), "name": n,
                             "suffix": n[len(b):].strip() if b and n.startswith(b) else n,
                             "floor": v.get("min_buyout"), "floor_qty": v.get("min_price_quantity"),
                             "qty": v.get("quantity_available"), "listings": v.get("listing_count"),
                             "market": v.get("market_price")})
            rows.sort(key=lambda r: (r["floor"] is None, r["floor"] or 0, r["suffix"]))
            out[iid] = {"status": "seen", "variants": rows, "variants_ts": s["timestamp"]}
    named = defaultdict(set)
    for e in trade:
        iid, n = e.get("id"), e.get("name") or ""
        b = base.get(iid)
        if b and n.startswith(b + " of "):
            named[iid].add(n[len(b) + 1:])
    for iid, ns in named.items():
        x = out.setdefault(iid, {"status": "seen", "variants": [], "variants_ts": None})
        x["trade_suffixes"] = sorted(ns)
    for iid, (o, _) in items.items():
        if iid not in out and o.get("quality") == 2 and o.get("class_id") in SUFFIX_CLASSES:
            out[iid] = {"status": "possible", "variants": [], "variants_ts": None}
    return out


def _pct(new, old):
    if old in (None, 0) or new is None:
        return None
    return round((new - old) / old * 100, 1)


def net_after_cut(price):
    return None if price is None else round(price * (1 - config.AH_CUT))


def movers(scans):
    """Price and supply change: latest vs previous scan, and latest vs the scan nearest 24 hours before."""
    if len(scans) < 2:
        return {"rows": [], "latest_ts": scans[-1]["timestamp"] if scans else None, "prev_ts": None, "base24_ts": None}
    last, prev = scans[-1], scans[-2]
    target = last["timestamp"] - 86400
    older = [s for s in scans if s["timestamp"] <= target]
    base = older[-1] if older else scans[0]
    rows = []
    for iid in set(last["items"]) | set(prev["items"]) | set(base["items"]):
        L, P, B = last["items"].get(iid), prev["items"].get(iid), base["items"].get(iid)
        ref = L or P or B
        floor = L["min_buyout"] if L else None
        qty = L["quantity_available"] if L else 0
        pf = P["min_buyout"] if P else None
        pq = P["quantity_available"] if P else 0
        bf = B["min_buyout"] if B else None
        bq = B["quantity_available"] if B else 0
        rows.append({
            "item_id": iid, "name": ref["item_name"], "class_id": ref.get("class_id"),
            "floor": floor, "qty": qty, "prev_floor": pf, "prev_qty": pq,
            "floor_chg_pct": _pct(floor, pf), "qty_chg": qty - pq, "qty_chg_pct": _pct(qty, pq),
            "base_floor": bf, "base_qty": bq,
            "floor_chg24_pct": _pct(floor, bf), "qty_chg24": qty - bq, "qty_chg24_pct": _pct(qty, bq),
            # supply-fall signal: at least 20 listed before, and 40 % or more gone now
            "supply_fall": pq >= 20 and qty <= pq * 0.6,
            "supply_fall24": bq >= 20 and qty <= bq * 0.6,
        })
    rows.sort(key=lambda r: (r["qty_chg_pct"] if r["qty_chg_pct"] is not None else 0, r["name"] or ""))
    return {"rows": rows, "latest_ts": last["timestamp"], "prev_ts": prev["timestamp"],
            "base24_ts": base["timestamp"], "base24_hours": round((last["timestamp"] - base["timestamp"]) / 3600, 1)}


def latest_items(scans):
    """Item -> (record, scan timestamp) from the newest scan that lists it. Also the newest scan alone."""
    seen = {}
    for s in scans:
        for iid, o in s["items"].items():
            seen[iid] = (o, s["timestamp"])
    return seen


def beta_medians(beta_scans):
    acc = defaultdict(list)
    for s in beta_scans:
        for iid, o in s["items"].items():
            if o.get("min_buyout") is not None:
                acc[iid].append((o["min_buyout"], o.get("quantity_available") or 0))
    return {iid: {"beta_floor_median": round(statistics.median(p for p, _ in v)),
                  "beta_qty_median": round(statistics.median(q for _, q in v)), "beta_scans": len(v)}
            for iid, v in acc.items()}


def trade_evidence(trade, realm):
    ev = defaultdict(lambda: {"sold": 0, "sold_units": 0, "sold_net": 0, "returned": 0})
    for e in trade:
        if e.get("realm") != realm or not e.get("id"):
            continue
        x = ev[e["id"]]
        if e.get("kind") == "sold":
            x["sold"] += 1
            x["sold_units"] += e.get("count") or 0
            x["sold_net"] += e.get("net") or 0
        elif e.get("kind") == "returned":
            x["returned"] += 1
    for x in ev.values():
        n = x["sold"] + x["returned"]
        x["sell_through"] = round(x["sold"] / n, 3) if n else None
    return ev


def name_index(items, trade, offers, beta_scans):
    """Item id -> name. Live scan names win, then trade log, vendor offers, beta scans."""
    names = {}
    for s in beta_scans:
        for iid, o in s["items"].items():
            names[iid] = o.get("item_name")
    for o in offers.values():
        if o.get("name"):
            names[o["item_id"]] = o["name"]
    for e in trade:
        if e.get("id") and e.get("name"):
            names[e["id"]] = e["name"]
    for iid, (o, _) in items.items():
        if o.get("item_name"):
            names[iid] = o["item_name"]
    return names


def watchlist(ids, live_scans, items, names=None):
    last = live_scans[-1] if live_scans else None
    rows = []
    for iid in ids:
        series = [{"ts": s["timestamp"], "floor": s["items"][iid]["min_buyout"], "qty": s["items"][iid]["quantity_available"]}
                  if iid in s["items"] else {"ts": s["timestamp"], "floor": None, "qty": 0} for s in live_scans]
        o, ts = items.get(iid, (None, None))
        cur = last["items"].get(iid) if last else None
        rows.append({"item_id": iid, "name": o["item_name"] if o else (names or {}).get(iid), "series": series,
                     "floor": cur["min_buyout"] if cur else None,
                     "floor_qty": cur["min_price_quantity"] if cur else None,
                     "qty": cur["quantity_available"] if cur else 0,
                     "listed_in_latest": bool(cur),
                     "last_seen_ts": ts, "last_seen_floor": o["min_buyout"] if o else None,
                     "vendor_sell": o.get("vendor_sell_price") if o else None})
    return rows


def recipe_board(offers, live_scans, beta_meds, live_ev, beta_ev):
    last = live_scans[-1]["items"] if live_scans else {}
    by_item = defaultdict(lambda: {"live": [], "beta": []})
    for o in offers.values():
        if not o.get("name") or not RECIPE.match(o["name"]) or not o.get("tradable") or o.get("cost") is None:
            continue
        by_item[o["item_id"]][o["kind"]].append(o)
    rows = []
    for iid, v in by_item.items():
        name = (v["live"] or v["beta"])[0]["name"]
        row = {"item_id": iid, "name": name}
        if v["live"]:
            best = min(v["live"], key=lambda o: o["cost"])
            row.update(cost=round(best["cost"]), cost_source="live", vendor=best["vendor"], location=best["location"],
                       stock_type=best["stock_type"], stock_limit=best["stock_limit"], vendor_seen_ts=best["timestamp"],
                       vendors=sorted({(o["vendor"], o["location"]) for o in v["live"]}))
        else:
            best = min(v["beta"], key=lambda o: o["cost"])
            row.update(cost=None, cost_source="beta", vendor=best["vendor"], location=best["location"],
                       stock_type=best["stock_type"], stock_limit=best["stock_limit"], vendor_seen_ts=best["timestamp"],
                       vendors=sorted({(o["vendor"], o["location"]) for o in v["beta"]}))
            row["beta_cost"] = round(best["cost"])
        row["in_capital"] = any((loc or "").startswith(config.CAPITALS) for _, loc in row["vendors"])
        a = last.get(iid)
        row["floor"] = a["min_buyout"] if a else None
        row["floor_qty"] = a["min_price_quantity"] if a else None
        row["qty"] = a["quantity_available"] if a else 0
        row["net_each"] = net_after_cut(row["floor"])
        row["margin"] = row["net_each"] - row["cost"] if row["net_each"] is not None and row["cost"] is not None else None
        row["roi_pct"] = round(row["margin"] / row["cost"] * 100) if row["margin"] is not None and row["cost"] else None
        e = live_ev.get(iid) or {}
        row.update(sold=e.get("sold", 0), returned=e.get("returned", 0), sell_through=e.get("sell_through"),
                   sold_net=e.get("sold_net", 0))
        b = beta_meds.get(iid) or {}
        be = beta_ev.get(iid) or {}
        row.update(beta_floor_median=b.get("beta_floor_median"), beta_qty_median=b.get("beta_qty_median"),
                   beta_sold=be.get("sold", 0), beta_returned=be.get("returned", 0), beta_sell_through=be.get("sell_through"))
        if row["cost"] is None and row["floor"] is not None:
            row["beta_margin_beta_cost"] = net_after_cut(row["floor"]) - row["beta_cost"]
        rows.append(row)
    rows.sort(key=lambda r: (r["margin"] is None, -(r["margin"] or 0), r["name"]))
    return rows


def parse_recipe_info(text):
    """AltsForever recipeInfo value: "<grey>;<Name>;,<item_id>:<count>,..." -> (grey, name, [(id, n)])."""
    grey, name, rest = text.split(";", 2)
    reagents = []
    for part in rest.split(","):
        if ":" in part:
            i, n = part.split(":")
            reagents.append((int(i), int(n)))
    return (int(grey) if grey.strip().isdigit() else None), name, reagents


def craft_margins(extracts, alts, items, offers, beta_meds):
    by_name = {}
    for iid, (o, ts) in items.items():
        by_name.setdefault(o["item_name"], iid)
    vendor_cost = {}
    for o in offers.values():
        if o["kind"] == "live" and o.get("cost") is not None:
            vendor_cost[o["item_id"]] = min(vendor_cost.get(o["item_id"], 1e18), o["cost"])

    def unit_cost(iid):
        if iid in vendor_cost:
            return round(vendor_cost[iid]), "vendor"
        if iid in items:
            return items[iid][0]["min_buyout"], "AH floor"
        return None, "no price"

    def product(name):
        iid = by_name.get(name)
        o, ts = items.get(iid, (None, None))
        return iid, (o["min_buyout"] if o else None), (o["quantity_available"] if o else None), ts

    def crafters(prof, skill):
        return sorted(n for n, c in alts.items() if (c.get("profs") or {}).get(prof) is not None
                      and (skill is None or c["profs"][prof] >= skill))

    rows = []
    seen = set()
    for ex in extracts:
        for prof in CRAFT_PROFESSIONS:
            for _, text in sorted((ex["recipe_info"].get(prof) or {}).items()):
                grey, name, reag = parse_recipe_info(text)
                if (prof, name) in seen:
                    continue
                seen.add((prof, name))
                acct_chars = sorted(n for n, c in alts.items() if c.get("account") == ex.get("account")
                                    and (c.get("profs") or {}).get(prof) is not None)
                rows.append(_craft_row(prof, name, None, grey, "Forever (AltsForever)",
                                       [(i, n, (items.get(i, ({"item_name": None}, 0))[0]["item_name"])) for i, n in reag],
                                       unit_cost, product, acct_chars, beta_meds))
    for prof, recipes in CLASSIC_CRAFTS.items():
        for name, (skill, reag) in recipes.items():
            if (prof, name) in seen:
                continue
            rows.append(_craft_row(prof, name, skill, None, "classic, unverified",
                                   [(by_name.get(r), n, r) for r, n in reag], unit_cost, product,
                                   crafters(prof, skill), beta_meds))
    rows.sort(key=lambda r: (r["profession"], r["margin"] is None, -(r["margin"] or 0)))
    herbs = []
    for h in HERBS + ENCHANT_MATS:
        iid = by_name.get(h)
        o, ts = items.get(iid, (None, None))
        herbs.append({"name": h, "item_id": iid, "group": "Enchanting mat" if h in ENCHANT_MATS else "Herb",
                      "floor": o["min_buyout"] if o else None, "qty": o["quantity_available"] if o else None,
                      "floor_qty": o["min_price_quantity"] if o else None, "seen_ts": ts,
                      "net_each": net_after_cut(o["min_buyout"]) if o else None,
                      "vendor_sell": o.get("vendor_sell_price") if o else None,
                      **({k: v for k, v in beta_meds.get(iid, {}).items()} if iid else {})})
    return {"rows": rows, "herbs": herbs}


def _craft_row(prof, name, skill, grey, source, reagents, unit_cost, product, who, beta_meds):
    parts, total, missing = [], 0, []
    for iid, n, rname in reagents:
        c, src = unit_cost(iid) if iid else (None, "no price")
        parts.append({"item_id": iid, "name": rname, "count": n, "unit_cost": c, "source": src})
        if c is None:
            missing.append(rname or str(iid))
        else:
            total += c * n
    pid, floor, qty, ts = product(name)
    cost = None if missing else total
    net = net_after_cut(floor)
    return {"profession": prof, "product": name, "product_id": pid, "skill_needed": skill, "grey_at": grey,
            "source": source, "reagents": parts, "reagent_cost": cost, "missing_prices": missing,
            "floor": floor, "qty": qty, "floor_seen_ts": ts, "net_each": net,
            "margin": net - cost if net is not None and cost is not None else None,
            "can_craft_now": who,
            "beta_floor_median": beta_meds.get(pid, {}).get("beta_floor_median") if pid else None}


def floor_vs_vendor(live_scans, beta_meds, min_qty=1):
    """Trade goods (class 7) in the newest scan, by AH floor / vendor sell price."""
    if not live_scans:
        return []
    rows = []
    for iid, o in live_scans[-1]["items"].items():
        vs = o.get("vendor_sell_price") or 0
        if o.get("class_id") != 7 or vs <= 0 or (o.get("quantity_available") or 0) < min_qty:
            continue
        b = beta_meds.get(iid) or {}
        rows.append({"item_id": iid, "name": o["item_name"], "floor": o["min_buyout"], "vendor_sell": vs,
                     "ratio": round(o["min_buyout"] / vs, 2), "qty": o["quantity_available"],
                     "floor_qty": o["min_price_quantity"],
                     # buy one at the floor, vendor it: the worst case
                     "worst_loss_each": vs - o["min_buyout"],
                     "worst_loss_floor_stack": (vs - o["min_buyout"]) * (o["min_price_quantity"] or 0),
                     "beta_floor_median": b.get("beta_floor_median"), "beta_qty_median": b.get("beta_qty_median"),
                     "beta_upside_each": (net_after_cut(b["beta_floor_median"]) - o["min_buyout"]) if b else None})
    rows.sort(key=lambda r: (r["ratio"], r["name"]))
    return rows


def book(chars, alts, trade, live_scans, items, names=None, suffix=None, owned=None):
    last = live_scans[-1]["items"] if live_scans else {}
    suffix, owned = suffix or {}, owned or {}
    names = dict(names or {})
    for iid, (o, _) in items.items():
        names.setdefault(iid, o["item_name"])

    def floor(iid):
        a = last.get(iid)
        return a["min_buyout"] if a else None

    inv = defaultdict(lambda: {"total": 0, "by_char": {}, "where": Counter()})
    char_rows = []
    # A character that only AltsForever knows (no Midas record yet) still counts: money from AltsForever.
    chars = dict(chars)
    for name in alts:
        chars.setdefault(name, {})
    for name in sorted(chars):
        c = chars[name]
        a = alts.get(name, {})
        # Midas writes an empty bank in the Forever client. AltsForever records the bank (bank bags too)
        # when the bank is open. Use the AltsForever bank unless Midas has a non-empty bank that is newer.
        bank, bank_ts = c.get("bank") or {}, c.get("bankTs")
        if a.get("bankAt") and (not bank or a["bankAt"] >= (bank_ts or 0)):
            bank, bank_ts = a.get("bank") or {}, a["bankAt"]
        src = {"bags": c.get("bags"), "bank": bank, "mail": c.get("mail")}
        units = 0
        for where in ("bags", "bank", "mail"):
            for iid, n in (src[where] or {}).items():
                iid = int(iid)
                inv[iid]["total"] += n
                inv[iid]["by_char"][name] = inv[iid]["by_char"].get(name, 0) + n
                inv[iid]["where"][where] += n
                units += n
        newer_alts = (a.get("updated") or 0) > _char_ts(c) and a.get("money") is not None
        money = a["money"] if newer_alts else c.get("money")
        char_rows.append({"name": name, "role": "main" if name in MAINS else "alt",
                          "class": a.get("class"), "level": a.get("level"), "profs": a.get("profs"),
                          "money": money, "money_source": "AltsForever" if newer_alts else "Midas",
                          "money_ts": a.get("updated") if newer_alts else _char_ts(c), "units": units,
                          "bags_ts": c.get("bagsTs"), "bank_ts": bank_ts, "mail_ts": c.get("mailTs"),
                          "auctions_ts": c.get("auctionsTs"), "account": a.get("account")})
    inv_rows = []
    for iid, v in inv.items():
        f = floor(iid)
        sx = suffix.get(iid)
        held = sorted({e.get("item_name") for c in owned.values() for w in ("bags", "bank", "mail")
                       for e in ((c.get(w) or {}).get("items") or {}).values() if e.get("item_id") == iid} - {None})
        inv_rows.append({"item_id": iid, "name": names.get(iid), "count": v["total"], "by_char": v["by_char"],
                         "where": dict(v["where"]), "floor": f,
                         # The floor is the lowest listing of ANY suffix: a low bound for suffix gear.
                         "floor_mixed": bool(sx), "suffix_status": sx["status"] if sx else None, "suffixes_held": held,
                         "value_net": net_after_cut(f) * v["total"] if f is not None else None})
    inv_rows.sort(key=lambda r: (-(r["value_net"] or 0), r["name"] or ""))
    auctions = []
    for name in sorted(chars):
        c = chars[name]
        for iid, a in (c.get("auctions") or {}).items():
            iid = int(iid)
            qty, gold = a.get("qty") or 0, a.get("gold") or 0
            each = round(gold / qty) if qty else None
            f = floor(iid)
            row = {"char": name, "item_id": iid, "name": names.get(iid), "qty": qty, "buyout_total": gold,
                   "buyout_each": each, "floor": f, "undercut": f is not None and each is not None and f < each,
                   "net_if_sold": net_after_cut(gold), "auctions_ts": c.get("auctionsTs"),
                   "suffix_status": None, "suffix_name": None, "undercut_note": None}
            sx = suffix.get(iid)
            if sx:
                # Midas keys auctions by item id only. The item-id floor can be another suffix.
                row["suffix_status"] = sx["status"]
                mine = {k: e for k, e in (((owned.get(name) or {}).get("auctions") or {}).get("items") or {}).items()
                        if e.get("item_id") == iid}
                vmap = {v["key"]: v for v in sx["variants"]}
                if len(mine) == 1:
                    k, e = next(iter(mine.items()))
                    row["suffix_name"] = e.get("item_name")
                    v = vmap.get(k)
                    if v and v.get("floor") is not None:
                        row["floor"] = v["floor"]
                        row["undercut"] = each is not None and v["floor"] < each
                        row["undercut_note"] = "same suffix"
                    else:
                        row["undercut"], row["undercut_note"] = None, "suffix not in the newest variant scan"
                else:
                    row["undercut"] = None
                    row["undercut_note"] = ("several suffixes of this item on this character" if mine
                                            else "suffix unknown: check it by hand")
            auctions.append(row)
    live = [e for e in trade if e.get("realm") == LIVE_TRADE_REALM]
    per = defaultdict(lambda: {"sold": 0, "units": 0, "gross": 0, "fees": 0, "net": 0, "returned": 0,
                               "ah_units": 0, "ah_cost": 0, "vendor_units": 0, "vendor_cost": 0, "first": None, "last": None})
    for e in live:
        x = per[(e.get("id"), e.get("name"))]
        k, n = e.get("kind"), e.get("count") or 0
        if k == "sold":
            x["sold"] += 1
            x["units"] += n
            x["gross"] += e.get("gross") or 0
            x["fees"] += e.get("fee") or 0
            x["net"] += e.get("net") or 0
            x["first"] = e["ts"] if x["first"] is None else min(x["first"], e["ts"])
            x["last"] = e["ts"] if x["last"] is None else max(x["last"], e["ts"])
        elif k == "returned":
            x["returned"] += 1
        elif k == "bought":
            pre = "vendor" if e.get("src") == "vendor" else "ah"
            x[pre + "_units"] += n
            x[pre + "_cost"] += e.get("gross") or 0
    pnl = []
    for (iid, name), x in per.items():
        bu = x["ah_units"] + x["vendor_units"]
        bc = x["ah_cost"] + x["vendor_cost"]
        unit_cost = bc / bu if bu else None
        covered = bu >= x["units"] and x["units"] > 0
        pnl.append(dict(x, item_id=iid, name=name,
                        net_per_sale=round(x["net"] / x["sold"]) if x["sold"] else None,
                        net_per_unit=round(x["net"] / x["units"]) if x["units"] else None,
                        unit_cost=round(unit_cost) if unit_cost is not None else None,
                        sell_through=round(x["sold"] / (x["sold"] + x["returned"]), 3) if x["sold"] + x["returned"] else None,
                        realized=round(x["net"] - unit_cost * x["units"]) if covered else None,
                        cost_basis="purchases" if covered else ("partial" if bu and x["units"] else ("none" if x["units"] else "-"))))
    pnl.sort(key=lambda r: (-(r["net"] or 0), -(r["ah_cost"] + r["vendor_cost"])))
    totals = {
        "sales_net": sum(r["net"] for r in pnl), "sales_gross": sum(r["gross"] for r in pnl),
        "fees": sum(r["fees"] for r in pnl), "sales": sum(r["sold"] for r in pnl),
        "returns": sum(r["returned"] for r in pnl),
        "ah_buys": sum(r["ah_cost"] for r in pnl), "vendor_buys": sum(r["vendor_cost"] for r in pnl),
        "cash_flow": sum(e.get("net") or 0 for e in live),
        "realized_covered": sum(r["realized"] for r in pnl if r["realized"] is not None),
        "money": sum(r["money"] or 0 for r in char_rows),
        "inventory_value_net": sum(r["value_net"] or 0 for r in inv_rows),
        "inventory_unpriced_items": sum(1 for r in inv_rows if r["floor"] is None),
        "auctions_value_net": sum(a["net_if_sold"] or 0 for a in auctions),
        "auctions_suffix_check": sum(1 for a in auctions if a["undercut"] is None),
        "first_trade_ts": live[0]["ts"] if live else None, "last_trade_ts": live[-1]["ts"] if live else None,
    }
    return {"chars": char_rows, "inventory": inv_rows, "auctions": auctions, "pnl": pnl, "totals": totals}


def price_series(live_scans, trade, beta_meds, names, items):
    """Per item: every live scan that lists it, the live Midas trades, and the BETA reference."""
    pts = defaultdict(list)
    for s in live_scans:
        for iid, o in s["items"].items():
            pts[iid].append({"ts": s["timestamp"], "floor": o.get("min_buyout"), "qty": o.get("quantity_available"),
                             "floor_qty": o.get("min_price_quantity"), "market": o.get("market_price"),
                             "listings": o.get("listing_count")})
    trades = defaultdict(list)
    for e in trade:
        if e.get("realm") == LIVE_TRADE_REALM and e.get("id") and e.get("kind") in ("sold", "bought", "returned"):
            trades[e["id"]].append({"ts": e["ts"], "kind": e["kind"], "count": e.get("count") or 0,
                                    "net": e.get("net"), "gross": e.get("gross"), "char": (e.get("char") or "").split("-")[0],
                                    "src": "vendor" if e.get("src") == "vendor" else ("AH" if e["kind"] == "bought" else None)})
    out = {}
    for iid in set(pts) | set(trades):
        o = items.get(iid, (None, None))[0]
        out[iid] = {"item_id": iid, "name": names.get(iid), "points": pts.get(iid, []), "trades": trades.get(iid, []),
                    "vendor_sell": o.get("vendor_sell_price") if o else None,
                    "beta_floor_median": (beta_meds.get(iid) or {}).get("beta_floor_median"),
                    "scan_times": [s["timestamp"] for s in live_scans]}
    return out


# ---------------------------------------------------------------- top level

def build(extracts, fetch_status=None, now=None, pins=None, categories=None):
    now = now or int(dt.datetime.now().timestamp())
    live = merge_scans(extracts, "live")
    beta = merge_scans(extracts, "beta")
    offers = {}
    for ex in extracts:
        offers.update(ex["offers"])
    trade = merge_trade(extracts)
    live = fill_item_meta(live, beta, trade, extracts)
    chars = merge_chars(extracts)
    alts = merge_alts(extracts)
    owned = merge_owned(extracts)
    items = latest_items(live)
    bmeds = beta_medians(beta)
    live_ev = trade_evidence(trade, LIVE_TRADE_REALM)
    beta_ev = trade_evidence(trade, BETA_TRADE_REALM)
    pins = pins or {}
    midas_watch = {i for ex in extracts for i in ex["watch"]}
    removed = set(pins.get("remove") or [])
    names = name_index(items, trade, offers, beta)
    suffix = suffix_index(live, trade, items)
    watch_ids = sorted((midas_watch | set(pins.get("add") or [])) - removed)
    accounts = []
    for ex in extracts:
        own_live = [s["timestamp"] for s in ex["scans"] if s["kind"] == "live"]
        mt = [t for t in ex["file_mtime"].values() if t]
        fs = ((fetch_status or {}).get("accounts") or {}).get(ex["account"], {}).get("files", {})
        accounts.append({"account": ex["account"], "file_mtime": ex["file_mtime"],
                         "data_ts": max(mt) if mt else None,
                         "newest_live_scan_ts": max(own_live) if own_live else None,
                         "copied_at": max([f.get("copied_at") or 0 for f in fs.values()] or [0]) or None,
                         "copy_errors": {k: f["error"] for k, f in fs.items() if f.get("error")}})
    series = price_series(live, trade, bmeds, names, items)
    trade_by_id = {i: v["trades"] for i, v in series.items()}
    boards = []
    for cat in categories or []:
        b, cs = category.board(cat, live, names, trade_by_id)
        boards.append(b)
        series.update(cs)
    return {
        "generated_ts": now, "stale_seconds": config.STALE_SECONDS, "ah_cut": config.AH_CUT,
        "live_realm": config.LIVE_REALM, "beta_realm": config.BETA_REALM, "faction": config.FACTION,
        "beta_window_start_ts": config.BETA_LATE_START,
        "accounts": accounts,
        "fetch": {k: (fetch_status or {}).get(k) for k in ("g1", "g1_error", "last_attempt")},
        "scans": [{"ts": s["timestamp"], "items": len(s["items"])} for s in live],
        "beta_scans": [{"ts": s["timestamp"], "items": len(s["items"])} for s in beta],
        "movers": movers(live),
        "watchlist": [dict(r, pin_source="Midas watch" if r["item_id"] in midas_watch else "pinned here")
                      for r in watchlist(watch_ids, live, items, names)],
        "recipes": recipe_board(offers, live, bmeds, live_ev, beta_ev),
        "crafts": craft_margins(extracts, alts, items, offers, bmeds),
        "floor_vendor": floor_vs_vendor(live, bmeds),
        "book": book(chars, alts, trade, live, items, names, suffix, owned),
        "suffix": {str(k): v for k, v in suffix.items()},
        "variant_scans": sum(1 for s in live if s.get("variants")),
        "item_names": {str(k): v for k, v in names.items()},
        "categories": boards,
        # Served by /api/series one item at a time, not inside /api/data.
        "series": series,
    }


def build_from_disk(data_dir=config.DATA_DIR, now=None):
    extracts = []
    for acct in config.ACCOUNTS:
        d = os.path.join(data_dir, "current", acct)
        if not os.path.isdir(d):
            continue
        ex = load_account(d, os.path.join(data_dir, "cache"))
        ex["account"] = acct
        extracts.append(ex)
    try:
        status = _read_json(os.path.join(data_dir, "fetch-status.json"))
    except (OSError, ValueError):
        status = None
    try:
        pins = _read_json(os.path.join(data_dir, "pins.json"))
    except (OSError, ValueError):
        pins = None
    return build(extracts, status, now, pins, category.load(data_dir))


if __name__ == "__main__":
    d = build_from_disk()
    print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in d.items()}, indent=1))
