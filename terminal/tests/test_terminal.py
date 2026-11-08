"""Tests for Wrynnberg Terminal. Run: python3 -m unittest discover -s terminal/tests -v"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from terminal import config, fetch, history, model, serve  # noqa: E402

LIVE, BETA = config.LIVE_REALM, config.BETA_REALM
LT, BT = model.LIVE_TRADE_REALM, model.BETA_TRADE_REALM
T0 = 1793900000


def item(name, floor, qty, vendor=10, cls=7, fq=None):
    return {"item_name": name, "min_buyout": floor, "quantity_available": qty, "min_price_quantity": fq or qty,
            "listing_count": 1, "market_price": floor, "vendor_sell_price": vendor, "class_id": cls, "quality": 1}


def scan(realm, ts, items, faction="Alliance", complete=True):
    return {"realm": realm, "faction": faction, "timestamp": ts, "complete": complete, "items": items}


def trade(kind, iid, name, ts, net, count=1, char="Ash", realm=LT, src=None, gross=None, fee=0):
    e = {"kind": kind, "id": iid, "name": name, "ts": ts, "net": net, "count": count, "fee": fee,
         "gross": gross if gross is not None else abs(net), "char": f"{char}-{LIVE}", "realm": realm}
    if src:
        e["src"] = src
    return e


def offer(iid, name, cost, kind="live", vendor="Nyoma", loc="Teldrassil (57.2, 61.2)"):
    realm = LIVE if kind == "live" else BETA
    return {"item_id": iid, "vendor_buy_price": cost, "vendor_name": vendor, "vendor_location": loc,
            "offer_id": f"{kind}-{iid}-{vendor}", "stock_type": "unlimited", "timestamp": T0,
            "scope": f"1|{realm}|Alliance", "metadata": {"item_name": name, "tradable": True}}


SNAPPER = 6328


def fixture_dbs():
    """Two accounts. Both hold the same replicated scans; one holds a replicated trade log copy."""
    live_scans = [
        scan(LIVE, T0, {2842: item("Silver Bar", 150, 301), SNAPPER: item("Recipe: Longjaw Mud Snapper", 1400, 6, 1, 9)}),
        scan(LIVE, T0 + 3600, {2842: item("Silver Bar", 145, 268), SNAPPER: item("Recipe: Longjaw Mud Snapper", 1400, 5, 1, 9)}),
        scan(LIVE, T0 + 7200, {2842: item("Silver Bar", 179, 100), SNAPPER: item("Recipe: Longjaw Mud Snapper", 1400, 4, 1, 9)}),
    ]
    beta = [scan(BETA, config.BETA_LATE_START + 100, {2842: item("Silver Bar", 99999, 5), SNAPPER: item("Recipe: Longjaw Mud Snapper", 900, 33, 1, 9),
                                                      777: item("Beta Only Thing", 5000, 3)}),
            scan(BETA, config.BETA_LATE_START - 100, {2842: item("Silver Bar", 1, 1)}),            # too early: ignored
            scan(BETA, config.BETA_LATE_START + 200, {2842: item("Silver Bar", 2, 2)}, faction="Horde"),  # Horde: ignored
            scan("CAMWOW US2 Realm 4 PvP", T0, {2842: item("Silver Bar", 3, 3)})]                   # other realm: ignored
    log = [trade("bought", SNAPPER, "Recipe: Longjaw Mud Snapper", T0 - 50 + i, -360, src="vendor", gross=360) for i in range(4)]
    log += [trade("sold", SNAPPER, "Recipe: Longjaw Mud Snapper", T0 + 10, 1330, gross=1400, fee=70) for _ in range(2)]
    log += [trade("sold", SNAPPER, "Recipe: Longjaw Mud Snapper", T0 + 20, 1330, gross=1400, fee=70)]
    log += [trade("sold", SNAPPER, "Recipe: Longjaw Mud Snapper", T0, 999999, realm=BT)]   # beta sale: never live
    a = {"AHResearchDB": {"scans": live_scans + beta,
                          "vendorIntel": {"offers": {"o1": offer(SNAPPER, "Recipe: Longjaw Mud Snapper", 360),
                                                     "o2": offer(SNAPPER, "Recipe: Longjaw Mud Snapper", 400, vendor="Tharynn Bouden", loc="Elwynn Forest (41.9, 67.2)"),
                                                     "o3": offer(9001, "Pattern: Beta Thing", 100, kind="beta", loc="Stormwind City (53.4, 81.8)")}}},
         "MidasDB": {"trade": {"log": log}, "watch": {2842: True, 8364: True},
                     "chars": {f"Ash-{LIVE}": {"scope": LT, "money": 298, "bags": {2842: 3, SNAPPER: 1}, "bank": {}, "mail": {2842: 2},
                                                "auctions": {SNAPPER: {"qty": 2, "gold": 2800}}, "bagsTs": T0, "auctionsTs": T0},
                               f"Ash-{BETA}": {"scope": BT, "money": 10 ** 9, "bags": {2842: 999}}}},
         "AltsForeverDB": {"chars": {"Ash Example": {"realm": LIVE.replace(" ", ""), "class": "WARRIOR", "level": 14,
                                                       "profs": {"Alchemy": 34}, "money": 298, "updated": T0}},
                           "recipeInfo": {"Alchemy": {"mana well": "25;Mana Well;,2447:1,3371:1"}}}}
    # Account B: the same scans (replicated), a replicated copy of the live trade rows, its own extra sale.
    b = json.loads(json.dumps(a, default=str))
    b["AHResearchDB"]["scans"] = [dict(s, items={int(k): v for k, v in s["items"].items()}) for s in b["AHResearchDB"]["scans"]]
    b["MidasDB"]["trade"]["log"] = log + [trade("sold", 2842, "Silver Bar", T0 + 30, 170, char="Birch", gross=179, fee=9)]
    b["MidasDB"]["chars"] = {f"Birch-{LIVE}": {"scope": LT, "money": 380, "bags": {}, "bagsTs": T0 + 5}}
    b["MidasDB"]["watch"] = {}
    b["AltsForeverDB"]["chars"] = {"Birch Example": {"realm": LIVE.replace(" ", ""), "class": "DRUID", "level": 14, "profs": {}, "money": 380, "updated": T0},
                                   "Cedar Example": {"realm": LIVE.replace(" ", ""), "class": "ROGUE", "level": 5, "profs": {}, "money": 13, "updated": T0}}
    b["AltsForeverDB"]["recipeInfo"] = {}
    return a, b


def extracts():
    out = []
    for i, d in enumerate(fixture_dbs()):
        ex = model.extract_account(d["AHResearchDB"], d["MidasDB"], d["AltsForeverDB"])
        ex["account"] = ["acct-A", "acct-B"][i]
        ex["file_mtime"] = {"AHResearch": T0 + 7300, "Midas": T0 + 7300, "AltsForever": T0 + 7300}
        out.append(ex)
    return out


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


class Extract(unittest.TestCase):
    def test_realm_and_faction_filter(self):
        ex = extracts()[0]
        kinds = sorted((s["kind"], s["timestamp"]) for s in ex["scans"])
        self.assertEqual([k for k, _ in kinds].count("live"), 3)
        self.assertEqual([k for k, _ in kinds].count("beta"), 1, "only late-beta Alliance scans")
        self.assertEqual(set(ex["chars"]), {"Ash"}, "beta character excluded")
        self.assertTrue(all(e["realm"] in (LT, BT) for e in ex["trade"]))

    def test_recipe_info_parse(self):
        self.assertEqual(model.parse_recipe_info("95;Elixir of Minor Defense;,765:1,2447:1,3371:1"),
                         (95, "Elixir of Minor Defense", [(765, 1), (2447, 1), (3371, 1)]))


class Merge(unittest.TestCase):
    def test_scans_unique_by_timestamp(self):
        live = model.merge_scans(extracts(), "live")
        self.assertEqual([s["timestamp"] for s in live], [T0, T0 + 3600, T0 + 7200])

    def test_identical_scan_with_new_timestamp_is_one_scan(self):
        ex = extracts()
        copy = dict(ex[1]["scans"][0], timestamp=T0 + 14)
        ex[1]["scans"].append(copy)
        self.assertEqual(len(model.merge_scans(ex, "live")), 3)

    def test_trade_multiset_union(self):
        rows = model.merge_trade(extracts())
        live_sold = [e for e in rows if e["realm"] == LT and e["kind"] == "sold" and e["id"] == SNAPPER]
        self.assertEqual(len(live_sold), 3, "two identical rows stay two; the replicated copy adds none")
        self.assertEqual(len([e for e in rows if e["id"] == 2842]), 1, "account B's own row is kept")


class Panels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = model.build(extracts(), now=T0 + 8000)

    def test_supply_fall_signal(self):
        sb = next(r for r in self.d["movers"]["rows"] if r["item_id"] == 2842)
        self.assertEqual((sb["prev_qty"], sb["qty"], sb["qty_chg"]), (268, 100, -168))
        self.assertTrue(sb["supply_fall"])
        self.assertEqual(sb["base_qty"], 301, "fewer than 24 h of scans: base is the oldest scan")
        self.assertEqual(self.d["movers"]["base24_hours"], 2.0)

    def test_recipe_margin_uses_cheapest_live_vendor(self):
        r = next(r for r in self.d["recipes"] if r["item_id"] == SNAPPER)
        self.assertEqual((r["cost"], r["floor"], r["net_each"], r["margin"]), (360, 1400, 1330, 970))
        self.assertEqual((r["sold"], r["returned"], r["sell_through"]), (3, 0, 1.0))
        self.assertFalse(r["in_capital"])
        self.assertEqual(r["beta_floor_median"], 900)

    def test_beta_vendor_recipe_has_no_live_cost(self):
        r = next(r for r in self.d["recipes"] if r["item_id"] == 9001)
        self.assertIsNone(r["cost"])
        self.assertIsNone(r["margin"])
        self.assertEqual(r["beta_cost"], 100)
        self.assertTrue(r["in_capital"])

    def test_book_snapper(self):
        p = next(r for r in self.d["book"]["pnl"] if r["item_id"] == SNAPPER)
        self.assertEqual((p["sold"], p["net"], p["net_per_sale"], p["unit_cost"], p["realized"]), (3, 3990, 1330, 360, 3990 - 1080))
        t = self.d["book"]["totals"]
        self.assertEqual(t["sales_net"], 3990 + 170)
        self.assertEqual(t["vendor_buys"], 1440)
        self.assertEqual(t["cash_flow"], 3990 + 170 - 1440)
        self.assertEqual(t["money"], 298 + 380 + 13, "Cedar has no Midas record; AltsForever money counts")
        self.assertIn("Cedar", [c["name"] for c in self.d["book"]["chars"]])

    def test_inventory_and_auctions(self):
        inv = {r["item_id"]: r for r in self.d["book"]["inventory"]}
        self.assertEqual(inv[2842]["count"], 5)
        self.assertEqual(inv[2842]["value_net"], round(179 * 0.95) * 5)
        a = self.d["book"]["auctions"][0]
        self.assertEqual((a["qty"], a["buyout_total"], a["buyout_each"], a["undercut"]), (2, 2800, 1400, False))

    def test_watchlist_names_unlisted_items(self):
        w = {r["item_id"]: r for r in self.d["watchlist"]}
        self.assertEqual(len(w[2842]["series"]), 3)
        self.assertFalse(w[8364]["listed_in_latest"])
        self.assertEqual(w[2842]["pin_source"], "Midas watch")

    def test_craft_sources(self):
        rows = self.d["crafts"]["rows"]
        mw = next(r for r in rows if r["product"] == "Mana Well")
        self.assertEqual(mw["source"], "Forever (AltsForever)")
        self.assertEqual(mw["can_craft_now"], ["Ash"])
        self.assertTrue(all(r["source"] == "classic, unverified" for r in rows if r["product"] != "Mana Well"))
        mhp = next(r for r in rows if r["product"] == "Minor Healing Potion")
        self.assertEqual(mhp["can_craft_now"], ["Ash"])
        hp = next(r for r in rows if r["product"] == "Healing Potion")
        self.assertEqual(hp["can_craft_now"], [], "skill 110 needed, Ash has 34")

    def test_beta_never_in_live_values(self):
        """Every beta value sits under a beta_ key. Live values stay the same with or without beta data."""
        no_beta = extracts()
        for ex in no_beta:
            ex["scans"] = [s for s in ex["scans"] if s["kind"] != "beta"]
            ex["offers"] = {k: o for k, o in ex["offers"].items() if o["kind"] != "beta"}
            ex["trade"] = [e for e in ex["trade"] if e["realm"] != BT]
        d2 = model.build(no_beta, now=T0 + 8000)
        for (p1, v1) in walk({k: self.d[k] for k in ("movers", "book", "floor_vendor", "watchlist")}):
            if ".beta_" in p1:
                continue
            self.assertNotEqual(v1, 99999, p1)
            self.assertNotEqual(v1, 999999, p1)
        self.assertEqual(self.d["book"]["totals"], d2["book"]["totals"])
        self.assertEqual(self.d["movers"], d2["movers"])
        for r1 in self.d["recipes"]:
            r2 = next((r for r in d2["recipes"] if r["item_id"] == r1["item_id"]), None)
            if r2:
                for k in ("cost", "floor", "margin", "roi_pct", "sold", "returned", "sell_through"):
                    self.assertEqual(r1[k], r2[k], k)
        fv = next(r for r in self.d["floor_vendor"] if r["item_id"] == 2842)
        self.assertEqual(fv["ratio"], round(179 / 10, 2))
        self.assertIsNotNone(fv["beta_floor_median"])

    def test_price_series(self):
        sr = self.d["series"][SNAPPER]
        self.assertEqual([p["ts"] for p in sr["points"]], [T0, T0 + 3600, T0 + 7200])
        self.assertEqual([p["qty"] for p in sr["points"]], [6, 5, 4])
        kinds = [t["kind"] for t in sr["trades"]]
        self.assertEqual((kinds.count("sold"), kinds.count("bought")), (3, 4), "live trades only; the beta sale is not here")
        self.assertTrue(all(t["net"] != 999999 for t in sr["trades"]))
        self.assertEqual(sr["beta_floor_median"], 900)
        self.assertEqual(sr["scan_times"], [T0, T0 + 3600, T0 + 7200])

    def test_account_data_age(self):
        a = self.d["accounts"][0]
        self.assertEqual(a["data_ts"], T0 + 7300)
        self.assertEqual(self.d["stale_seconds"], 6 * 3600)


class Fetch(unittest.TestCase):
    def test_g1_asleep_keeps_last_copy(self):
        with tempfile.TemporaryDirectory() as d:
            p = fetch.local_path(d, list(config.ACCOUNTS)[0], "Midas")
            os.makedirs(os.path.dirname(p))
            open(p, "w").write("MidasDB = {}")

            def run(cmd, **kw):
                return subprocess.CompletedProcess(cmd, 255, "", "ssh: connect to host g1: No route to host")
            st = fetch.fetch(d, run=run, now=T0)
            self.assertEqual(st["g1"], "unreachable")
            self.assertTrue(os.path.exists(p))
            self.assertEqual(json.load(open(os.path.join(d, fetch.STATUS)))["g1"], "unreachable")

    def test_copy_only_reads_from_g1(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            def run(cmd, **kw):
                calls.append(cmd)
                if cmd[0] == "ssh":
                    rows = [{"p": f"C:\\Program Files (x86)\\World of Warcraft\\_forever_\\WTF\\Account\\{a}\\SavedVariables\\{f}.lua",
                             "t": T0, "n": 12} for a in config.ACCOUNTS.values() for f in config.FILES]
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")
                open(cmd[-1], "w").write("x" * 12)
                os.utime(cmd[-1], (T0, T0))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            st = fetch.fetch(d, run=run, now=T0)
            self.assertEqual(len(st["last_copied"]), len(config.ACCOUNTS) * len(config.FILES))
            for c in calls:
                if c[0] == "scp":
                    self.assertTrue(c[-2].startswith(config.G1_HOST + ":/" + config.G1_SV_DIR.split("{acct}")[0]))
                    self.assertTrue(c[-1].startswith(d), "destination is local")
                    self.assertIn("-p", c)
            # unchanged files are not copied again
            calls.clear()
            st = fetch.fetch(d, run=run, now=T0 + 60)
            self.assertEqual(st["last_copied"], [])
            self.assertEqual([c[0] for c in calls], ["ssh"])

    def test_remote_script_is_read_only(self):
        ps = fetch.remote_stat_script()
        for verb in ("Set-", "Remove-", "New-", "Out-File", "Copy-", "Move-", ">"):
            self.assertNotIn(verb, ps)

    def test_watch_copies_only_stable_new_files(self):
        with tempfile.TemporaryDirectory() as d:
            g1 = {"t": T0, "n": 12}
            calls = []

            def run(cmd, **kw):
                calls.append(cmd[0])
                if cmd[0] == "ssh":
                    rows = [{"p": f"C:\\WoW\\_forever_\\WTF\\Account\\{a}\\SavedVariables\\{f}.lua",
                             "t": g1["t"], "n": g1["n"]} for a in config.ACCOUNTS.values() for f in config.FILES]
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")
                with open(cmd[-1], "w") as fh:
                    fh.write("x" * g1["n"])
                os.utime(cmd[-1], (g1["t"], g1["t"]))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            # New files that stay the same over the settle time: copied.
            st = fetch.watch_once(d, run=run, sleep=lambda s: None)
            self.assertEqual(len(st["last_copied"]), len(config.ACCOUNTS) * len(config.FILES))
            # Nothing new: one stat, no copy.
            calls.clear()
            self.assertIsNone(fetch.watch_once(d, run=run, sleep=lambda s: None))
            self.assertEqual(calls, ["ssh"])

            # The game is still writing: the size changes during the settle time, so no copy yet.
            def grow(_):
                g1["n"] += 5
            g1["t"] += 60
            calls.clear()
            self.assertIsNone(fetch.watch_once(d, run=run, sleep=grow))
            self.assertNotIn("scp", calls)

    def test_watch_g1_asleep(self):
        with tempfile.TemporaryDirectory() as d:
            def run(cmd, **kw):
                return subprocess.CompletedProcess(cmd, 255, "", "no route")
            self.assertIsNone(fetch.watch_once(d, run=run, sleep=lambda s: None))


class Serve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = serve.make_server(0, cls.tmp.name)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def get(self, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10)

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_page_and_data(self):
        self.assertIn(b"Wrynnberg Terminal", self.get("/").read())
        d = json.loads(self.get("/api/data").read())
        self.assertEqual(d["live_realm"], LIVE)
        self.assertEqual(d["accounts"], [], "no copy yet: empty, not a crash")

    def test_series_endpoint(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/series?id=2842")
        self.assertEqual(e.exception.code, 404, "no data copied: 404, not a crash")
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/series?id=abc")
        self.assertEqual(e.exception.code, 400)
        d = json.loads(self.get("/api/data").read())
        self.assertNotIn("series", d, "series stay out of /api/data")

    def test_chart_library_served(self):
        body = self.get("/vendor/lightweight-charts.standalone.production.js").read()
        self.assertIn(b"Lightweight Charts", body[:400])

    def test_no_path_escape(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/../model.py")
        self.assertEqual(e.exception.code, 404)

    def test_pins_round_trip_and_foreign_origin(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/pins", data=b'{"add":[2842],"remove":[]}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        self.assertEqual(json.loads(urllib.request.urlopen(req, timeout=10).read())["add"], [2842])
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/pins", data=b'{"add":[1]}',
                                     headers={"Origin": "http://evil.example"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 403)


class EmptyNames(unittest.TestCase):
    """A scan of an item the game had not loaded has item_name "" and quality -1."""
    def test_empty_name_never_hides_a_known_name(self):
        blank = dict(item("", 120, 4, vendor=None, cls=None), quality=-1)
        a = {"AHResearchDB": {"scans": [scan(LIVE, T0, {2842: dict(item("Silver Bar", 150, 3), quality=2), 7284: blank}),
                                        scan(LIVE, T0 + 60, {2842: dict(item("", 140, 3, vendor=None, cls=None), quality=-1), 7284: blank,
                                                             9999: blank})]},
             "MidasDB": {"nameIndex": {"bard's trousers of the wolf": 7284}}, "AltsForeverDB": {}}
        ex = model.extract_account(a["AHResearchDB"], a["MidasDB"], a["AltsForeverDB"])
        ex["account"], ex["file_mtime"] = "acct-A", {"AHResearch": T0, "Midas": T0, "AltsForever": T0}
        d = model.build([ex], now=T0 + 120)
        mv = {r["item_id"]: r for r in d["movers"]["rows"]}
        self.assertEqual((mv[2842]["name"], mv[2842]["floor"], mv[2842]["class_id"]), ("Silver Bar", 140, 7))
        self.assertEqual(mv[7284]["name"], "Bard's Trousers of the Wolf")
        self.assertIsNone(mv[9999]["name"])
        self.assertEqual(d["item_names"]["2842"], "Silver Bar")
        # the cached extract is not changed
        self.assertEqual(ex["scans"][1]["items"][2842]["item_name"], "")


class Bank(unittest.TestCase):
    """Midas writes an empty bank in the Forever client. The book counts the AltsForever bank."""
    def build(self, midas_bank, alts_bank_at):
        ahr = {"scans": [scan(LIVE, T0, {2836: item("Coarse Stone", 110, 600)})]}
        midas = {"chars": {f"Ledger-{LIVE}": {"scope": model.LIVE_TRADE_REALM, "money": 100, "bags": {}, "bank": midas_bank,
                                                  "mail": {}, "bankTs": T0 + 50}}}
        alts = {"chars": {"Ledger Example": {"realm": LIVE.replace(" ", ""), "money": 100, "updated": T0 + 60,
                                                   "bank": {2836: 140}, "bankAt": alts_bank_at},
                          "Ledger Beta": {"realm": BETA.replace(" ", ""), "bank": {2836: 999}, "bankAt": T0}}}
        ex = model.extract_account(ahr, midas, alts)
        ex["account"], ex["file_mtime"] = "acct-A", {"AHResearch": T0, "Midas": T0, "AltsForever": T0}
        d = model.build([ex], now=T0 + 120)
        return {r["item_id"]: r for r in d["book"]["inventory"]}

    def test_alts_bank_counts_when_midas_bank_is_empty(self):
        inv = self.build({}, T0 - 3600)
        self.assertEqual((inv[2836]["count"], dict(inv[2836]["where"])), (140, {"bank": 140}))

    def test_newer_non_empty_midas_bank_wins(self):
        inv = self.build({2836: 20}, T0 - 3600)
        self.assertEqual(inv[2836]["count"], 20)


class Category(unittest.TestCase):
    """A category index follows the shared floors. A new item does not move it."""
    def test_index_and_scan_members(self):
        from terminal import category
        cat = {"key": "2h-str-leveling", "id": -1, "label": "2H", "req": [15, 32],
               "bands": [{"id": -2, "label": "low", "req": [15, 21]}],
               "items": {1: {"type": "2H axe", "req": 18, "name": "A"}, 2: {"type": "2H mace", "req": 19, "name": "B"},
                         3: {"type": "2H sword", "req": 20, "name": "C"}, 4: {"type": "polearm", "req": 30, "name": "D"}}}
        g = lambda f: item("x", f, 2)
        s1 = scan(LIVE, T0, {1: g(1000), 2: g(2000), 3: g(4000), 99: g(5)})
        s2 = scan(LIVE, T0 + 3600, {1: g(500), 2: g(1000), 3: g(2000), 4: g(900000), 99: g(5),
                                    3201: g(6000)})       # 4 is new: no move; 3201 comes from variants
        s2["variants"] = {"3201:b1": {"item_id": 3201, "class_id": 2, "subclass_id": 1, "equip_loc": "INVTYPE_2HWEAPON",
                                      "required_level": 23, "quality": 2},
                          "5:b1": {"item_id": 5, "class_id": 2, "subclass_id": 10, "equip_loc": "INVTYPE_2HWEAPON",
                                   "required_level": 23}}   # a staff: not in the category
        b, series = category.board(cat, [s1, s2], {}, {1: [{"ts": T0, "kind": "bought"}]})
        self.assertEqual([p["floor"] for p in series[-1]["points"]], [2000, 1000])
        self.assertEqual(series[-1]["points"][-1]["floor_qty"], 5)
        self.assertEqual([p["floor"] for p in series[-2]["points"]], [2000, 1000])
        self.assertEqual(len(series[-1]["trades"]), 1)
        ids = {r["item_id"] for r in b["rows"]}
        self.assertIn(3201, ids)
        self.assertNotIn(5, ids)
        self.assertNotIn(99, ids)


class Suffix(unittest.TestCase):
    """Random-suffix gear: the item-id floor mixes suffixes, so undercut needs the floor of your suffix."""
    AXE, BANDS, CLUB = 15269, 15306, 1000

    def dbs(self, variants=True, owned=True):
        g = lambda name, floor, cls=2: dict(item(name, floor, 3, cls=cls), quality=2)
        items = {self.AXE: g("Massive Battle Axe", 4500), self.BANDS: g("Feral Bindings", 900, 4), self.CLUB: g("Spiked Club", 800)}
        var = {"15269:b5001": {"item_id": self.AXE, "suffix_id": "b5001", "item_name": "Massive Battle Axe of Power", "min_buyout": 26000,
                               "min_price_quantity": 1, "quantity_available": 1, "listing_count": 1, "market_price": 26000},
               "15269:b5002": {"item_id": self.AXE, "suffix_id": "b5002", "item_name": "Massive Battle Axe of the Whale", "min_buyout": 4500,
                               "min_price_quantity": 1, "quantity_available": 2, "listing_count": 2, "market_price": 4500},
               "15306:b12721": {"item_id": self.BANDS, "suffix_id": "b12721", "item_name": "Feral Bindings of the Monkey", "min_buyout": 900,
                                "min_price_quantity": 1, "quantity_available": 1, "listing_count": 1, "market_price": 900}}
        sc = scan(LIVE, T0, items)
        if variants:
            sc["variants"] = var
        own = {f"{LIVE}|Vault": {"faction": "Alliance",
                                  "auctions": {"ts": T0, "items": {"15269:b5001": {"item_id": self.AXE, "suffix_id": "b5001", "item_name": "Massive Battle Axe of Power", "count": 1, "buyout": 25000}}},
                                  "bank": {"ts": T0, "items": {"15306:b12721": {"item_id": self.BANDS, "suffix_id": "b12721", "item_name": "Feral Bindings of the Monkey", "count": 1}}}},
               f"{BETA}|Vault": {"faction": "Alliance", "bags": {"ts": T0, "items": {"1:2": {"item_id": 1, "suffix_id": 2, "item_name": "beta", "count": 1}}}}}
        ahr = {"scans": [sc], "owned": own if owned else {}}
        midas = {"trade": {"log": [trade("sold", self.CLUB, "Spiked Club of Magic", T0, 950)]},
                 "chars": {f"Vault-{LIVE}": {"scope": LT, "money": 1, "bank": {self.BANDS: 1},
                                              "auctions": {self.AXE: {"qty": 1, "gold": 25000}, self.CLUB: {"qty": 1, "gold": 9000}},
                                              "auctionsTs": T0}}}
        ex = model.extract_account(ahr, midas, {})
        ex["account"], ex["file_mtime"] = "acct-A", {"AHResearch": T0, "Midas": T0, "AltsForever": T0}
        return model.build([ex], now=T0 + 60)

    def test_extract_keeps_variants_and_live_owned_only(self):
        d = self.dbs()
        self.assertEqual(d["variant_scans"], 1)
        S = d["suffix"]
        self.assertEqual([v["suffix"] for v in S[str(self.AXE)]["variants"]], ["of the Whale", "of Power"])
        self.assertEqual(S[str(self.CLUB)], {"status": "seen", "variants": [], "variants_ts": None, "trade_suffixes": ["of Magic"]})

    def test_undercut_uses_floor_of_own_suffix(self):
        a = {r["item_id"]: r for r in self.dbs()["book"]["auctions"]}
        axe = a[self.AXE]
        # Item-id floor 45s (a Whale) is under 2g50, but the Power floor is 2g60: not undercut.
        self.assertEqual((axe["floor"], axe["undercut"], axe["undercut_note"], axe["suffix_name"]),
                         (26000, False, "same suffix", "Massive Battle Axe of Power"))
        club = a[self.CLUB]
        self.assertIsNone(club["undercut"])
        self.assertEqual(club["undercut_note"], "suffix unknown: check it by hand")

    def test_without_addon_data_no_false_undercut(self):
        d = self.dbs(variants=False, owned=False)
        axe = {r["item_id"]: r for r in d["book"]["auctions"]}[self.AXE]
        self.assertEqual((axe["floor"], axe["undercut"]), (4500, None))
        self.assertEqual(d["book"]["totals"]["auctions_suffix_check"], 2)
        inv = {r["item_id"]: r for r in d["book"]["inventory"]}[self.BANDS]
        self.assertTrue(inv["floor_mixed"])
        self.assertEqual(d["suffix"][str(self.BANDS)]["status"], "possible")

    def test_inventory_lists_suffixes_held(self):
        inv = {r["item_id"]: r for r in self.dbs()["book"]["inventory"]}[self.BANDS]
        self.assertEqual(inv["suffixes_held"], ["Feral Bindings of the Monkey"])

    def test_cache_key_has_extract_version(self):
        with tempfile.TemporaryDirectory() as t:
            acct, cache = os.path.join(t, "acct-A"), os.path.join(t, "cache")
            os.makedirs(acct)
            open(os.path.join(acct, "Midas.lua"), "w").close()
            model.load_account(acct, cache, loader=lambda p: {})
            first = os.listdir(cache)
            old = model.EXTRACT_VERSION
            try:
                model.EXTRACT_VERSION = old + 1
                model.load_account(acct, cache, loader=lambda p: {})
            finally:
                model.EXTRACT_VERSION = old
            self.assertNotEqual(first, os.listdir(cache))


class OldStarts(unittest.TestCase):
    L = history.LIVE_LAUNCH_TS

    def starts(self):
        # Two old starts: price falls from 100 to 50 over 48 h. Start B is at twice the gold level.
        a = {"key": "a", "label": "A", "launch_ts": 1, "items": {2770: [[h, 100 - h, None] for h in range(0, 49)]}}
        b = {"key": "b", "label": "B", "launch_ts": 2, "items": {2770: [[h, 2 * (100 - h), None] for h in range(0, 49)]}}
        return [a, b]

    def test_average_is_geometric_and_on_live_clock(self):
        o = history.overlay(self.starts(), 2770, [])
        self.assertEqual([s["key"] for s in o["starts"]], ["a", "b"])
        ts, p, n = o["avg"][0]
        self.assertEqual(ts, self.L + 3 * 3600)           # center of the first 6-hour bucket
        self.assertEqual(n, 2)
        # Bucket 0-6 h: medians 97.5 and 195 (h 0..5). Geometric mean = 97.5 * sqrt(2).
        self.assertAlmostEqual(p, 97.5 * 2 ** 0.5, delta=1)
        self.assertEqual(o["match"], {"n": 0})
        self.assertIsNone(history.overlay(self.starts(), 9999, []))

    def test_match_follows_and_scales(self):
        o0 = history.overlay(self.starts(), 2770, [])
        avg = [[(t - self.L) / 3600, p] for t, p, _ in o0["avg"]]
        # Live floor is exactly 3x the old average at each scan time, so the shape matches.
        live = [{"ts": self.L + int(h * 3600), "floor": int(3 * history.interp(avg, h))} for h in range(4, 44, 4)]
        o = history.overlay(self.starts(), 2770, live)
        m = o["match"]
        self.assertEqual(m["n"], len(live))
        self.assertAlmostEqual(m["ratio"], 3.0, delta=0.02)
        self.assertGreater(m["r"], 0.99)
        self.assertTrue(m["same_direction"])
        self.assertLess(m["live_change"], 0)
        self.assertAlmostEqual(o["avg_scaled"][2][1], o["avg"][2][1] * m["ratio"], delta=2)

    def test_opposite_direction(self):
        live = [{"ts": self.L + h * 3600, "floor": 100 + 10 * h} for h in range(4, 44, 4)]
        m = history.overlay(self.starts(), 2770, live)["match"]
        self.assertFalse(m["same_direction"])
        self.assertLess(m["r"], 0)

    def test_bad_points_skipped_and_files_load(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "history"))
            with open(os.path.join(d, "history", "x.json"), "w") as fh:
                json.dump({"key": "x", "launch_ts": 5, "items": {"2770": [[1, 0, None], [2, None, None], [-3, 50, None],
                                                                         [99999, 50, None], [7, 80, 3]]}}, fh)
            with open(os.path.join(d, "history", "bad.json"), "w") as fh:
                fh.write("{not json")
            h = history.History(d)
            self.assertEqual([s["key"] for s in h.load()], ["x"])
            o = h.overlay(2770, [])
            self.assertEqual(o["avg"], [[self.L + 9 * 3600, 80, 1]])   # only the 7 h point is valid


if __name__ == "__main__":
    unittest.main()
