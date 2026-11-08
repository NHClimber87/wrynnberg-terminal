"""Constants and paths for Wrynnberg Terminal.

Your own setup (accounts, characters, game PC, data folder) goes in a JSON file outside the
repository: $WRYNNBERG_CONFIG, else ~/.config/wrynnberg-terminal/config.json. See
config.example.json. Without that file the terminal uses the example values below.
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
CONFIG_PATH = os.environ.get("WRYNNBERG_CONFIG",
                             os.path.expanduser("~/.config/wrynnberg-terminal/config.json"))


def _local():
    try:
        with open(CONFIG_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


_L = _local()

DATA_DIR = os.environ.get("WRYNNBERG_DATA", os.path.expanduser(_L.get("data_dir", "~/wrynnberg-data")))

LIVE_REALM = _L.get("live_realm", "CAMWOW US2 Realm 11 RP")
BETA_REALM = _L.get("beta_realm", "Classic Beta PvE 2")
FACTION = _L.get("faction", "Alliance")
# Late beta window used for the BETA reference prices (October 10, 2026 and later).
BETA_LATE_START = 1791590400

AH_CUT = 0.05
STALE_SECONDS = 6 * 3600

# Local folder name -> account folder name under WTF/Account on the game PC.
ACCOUNTS = _L.get("accounts", {"acct-1": "ACCOUNT1", "acct-2": "ACCOUNT2"})
# Your own characters. Any other live character is shown as an AH or bank alt.
MAINS = set(_L.get("mains", []))
FILES = ("AHResearch", "Midas", "AltsForever")
# ssh host name of the Windows game PC (an entry in ~/.ssh/config), and its SavedVariables folder.
G1_HOST = _L.get("game_pc_host", "gamepc")
G1_SV_DIR = _L.get("saved_variables_dir",
                   "C:/Program Files (x86)/World of Warcraft/_forever_/WTF/Account/{acct}/SavedVariables")

CAPITALS = ("Stormwind City", "Ironforge", "Darnassus")
