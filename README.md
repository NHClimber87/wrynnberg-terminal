# Wrynnberg Terminal

Wrynnberg Terminal is a local market terminal for one WoW Forever realm and faction. It reads copies of the
SavedVariables files of one or more accounts. It shows the AH position of all accounts on one page.

The terminal is read only. It does not automate the game, post auctions or act on the AH.
Blizzard forbids AH automation.

## Requirements

- Python 3.10 or later. The terminal uses only the standard library.
- On the game PC: the add-ons Midas, AltsForever and AHResearch (the AHResearch recorder is not in this
  repository). The terminal reads their SavedVariables files.
- The game PC is a Windows PC with an OpenSSH server. The terminal reads it with `ssh` and `scp` only.

## Setup

1. Copy `config.example.json` to `~/.config/wrynnberg-terminal/config.json`. Set `WRYNNBERG_CONFIG` to use
   another path.
2. Put your account folder names (the folders under `WTF/Account`) in `accounts`.
3. Put your own character names in `mains`. Other live characters show as AH or bank alts.
4. Set `game_pc_host` to the ssh host name of the game PC. Make sure that `ssh <host>` works with a key.

The config file stays outside the repository. Do not commit it.

## Run

```bash
python3 -m terminal.serve --port 8744 --fetch
```

Open `http://127.0.0.1:8744/`. The server listens on 127.0.0.1 only.

In this README, "G1" is the game PC.

- `--fetch` copies changed files from G1 before the server starts.
- The server looks at the G1 file times every 120 seconds (`--watch SECONDS`, `0` = off). If a file is new and
  stays the same for 15 seconds, the server copies it. The open page loads the new data in 15 seconds or less.
- So: scan with Midas, then `/reload`. The terminal shows the scan in about 2 minutes.
- Press `r` on the page to copy changed files at once.
- Press `?` on the page for the keyboard keys.

## Price chart

Press `c` (or Enter on a row) for the chart layout. The list is on the left, the chart is on the right,
and a second panel is under the list. The chart follows the list cursor.

- `1`–`6` picks the list. `Shift`+`1`–`6` picks the panel under it. `Tab` swaps the two. `b` shows the next panel under the list.
- `a` holds the chart item as a compare line. Move the cursor, and the new item draws against it on a % scale. `x` clears compare.
- `f` fits all data. `l` sets a log price scale. Scroll zooms, drag pans. `Esc` closes the chart layout.
- Markers: S = your sales (net each), B = your buys (price each), R = returns. Each marker groups 30 minutes of
  Midas rows. Sale times are mail-read times.
- Lines: floor (gold), market (option), vendor sell, and the late-beta floor (BETA). Bars: quantity listed.
- `#chart=<item id>,list=<1-6>,dock=<1-6>` opens the chart layout on one item.

### Old fresh starts

`o` (or the "Old starts" button) shows past fresh Classic starts on the live clock. Hour 0 is the live launch,
2026-11-04 15:00 PST. "Each start" shows one thin line for each start.

- The pink dashed line is the geometric mean of all starts at the same hours after launch. Buckets are 6 hours
  for the first week, then 1 day.
- The line is scaled by "live ×N of old", the median ratio of the live floor to the old average.
- "shape r" is the correlation of log prices over the overlap. "same way" compares the price change from
  the first third of the overlap to the last third.
- "All" shows the live data and the next 48 hours of the old pattern.

Each start is one file, `data/terminal/history/<key>.json`:

```json
{"key": "sod-2023", "label": "Season of Discovery (2023)", "launch_ts": 1701388800,
 "realm": "...", "faction": "...", "source": "...", "source_url": "...",
 "resolution": "hourly", "price_kind": "min",
 "items": {"2770": [[hours_since_launch, price_copper, quantity_or_null], ...]}}
```

The server reads new files at the next chart request. A file that does not parse is skipped.

The chart uses TradingView Lightweight Charts 4.2.3 (Apache 2.0), stored in `static/vendor/`.

## Data flow

1. In the game, run the scan. Then do `/reload` or log out. The game writes SavedVariables only then.
2. `terminal/fetch.py` asks G1 for the file times and sizes. The request is one read-only PowerShell query.
3. `scp -p` copies each changed file from G1 to `data/terminal/current/<account>/`. The copy keeps the G1 file time.
4. `terminal/model.py` parses each copy once and caches the result in `data/terminal/cache/`.
5. `terminal/serve.py` serves the page and `/api/data`.

The data folder is `data_dir` in the config file (default `~/wrynnberg-data`). Set `WRYNNBERG_DATA` to use
another folder.

If G1 is asleep, the last copy stays. The header shows "G1 asleep" and the copy age. A copy older than
6 hours shows STALE.

## Rules in the model

- Live is `live_realm` and beta is `beta_realm` (defaults `CAMWOW US2 Realm 11 RP` and `Classic Beta PvE 2`),
  both for `faction`. Beta prices count from October 10, 2026. The two never merge.
- Beta values have keys that start with `beta_`. They never enter a live total, average or margin.
  The page marks each beta value BETA.
- Each account holds a full copy of the AHResearch scans. Scans are unique by timestamp. Two scans with the
  same item data are one scan.
- Midas trade rows merge as a multiset union. An identical row counts as often as the account that holds it most.
- Quantity listed is supply. Only Midas `sold` and `returned` rows are sales evidence.
- Midas sale times are mail-read times, not sale times.
- "net" is the price after the 5% AH cut. Deposits are not included.
- Craft recipes from AltsForever `recipeInfo` are Forever data. The first number in a `recipeInfo` value is
  shown as "grey". The meaning of that number is not confirmed. Classic recipes show "classic, unverified".
- Pins: the Midas watch list of each account, plus items pinned on the page (`data/terminal/pins.json`).

## Random-suffix gear

Midas and the AHResearch item rows key the AH by item id. "Massive Battle Axe of Power" and
"Massive Battle Axe of the Whale" have one floor there: the lowest listing of any suffix.

- `SFX` tag: suffixes are known (AHResearch variants or a suffix name in the trade log). Hover the tag for the floor of each suffix.
- `sfx?` tag: a green weapon or armor with no suffix data yet. Check the suffix by hand.
- Auctions: if AHResearch knows the suffix of your auction, "Undercut" uses the floor of that suffix. Otherwise it shows "check suffix".
- Inventory: the floor and value of suffix gear show "≥". The value is a low bound.

AHResearch 1.1.0 records no suffix in WoW Forever. The Forever client puts the suffix in the bonus IDs of the item
link (`item:15306::::::::18:1485:::1:12721`), not in field 7. AHResearch 1.1.1 reads both. Its scan message
then shows "snapshot recorded with N suffix variants", with N above 0.

## Item categories

A category is a group of items with one price index. The Watchlist panel shows one tab for each
category. The first rows of the tab are index rows. Press Enter on an index row for its chart.

- The index is chain-linked. At each scan it moves by the trimmed mean change of the floors of the
  items that the scan shares with the last known floors. A new or gone item does not move it.
- The index starts at the median floor of the first scan, so it reads as copper.
- The chart markers on an index are your trades in the whole group.

Category files are in `data/terminal/categories/<key>.json`. To make the 2H Strength weapon category
(two-hand axes, maces, swords and polearms, required level 15-32), run this command:

```bash
python3 -m terminal.make_category --polestar <Polestar_ItemDB folder>
```

The command reads `Items.lua` (Classic Era client tables) and `Forever.lua` (ForeverLevelingBiS, MIT).
Scan variants add new Forever items with the same weapon type, slot and level range.

## Tests

```bash
python3 -m unittest discover -s terminal/tests -v
```
