"""Copy SavedVariables from G1 to DATA_DIR/current/<account>/.

G1 is read only: this module runs one read-only PowerShell query (file times and sizes)
and `scp` copies FROM G1. It never writes to G1. If G1 does not answer, the last copy stays
and fetch-status.json records the failure, so the page shows the old copy as stale.
"""
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from terminal import config  # noqa: E402

STATUS = "fetch-status.json"


def _read_json(path):
    with open(path) as fh:
        return json.load(fh)


def encoded(ps):
    return base64.b64encode(ps.encode("utf-16-le")).decode("ascii")


def remote_stat_script():
    paths = []
    for acct in config.ACCOUNTS.values():
        d = config.G1_SV_DIR.format(acct=acct).replace("/", "\\")
        for f in config.FILES:
            paths.append(f"{d}\\{f}.lua")
    arr = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    return ("$ErrorActionPreference='SilentlyContinue';"
            f"$o=foreach($p in @({arr})){{$i=Get-Item -LiteralPath $p;"
            "if($i){[pscustomobject]@{p=$p;t=[int64](($i.LastWriteTimeUtc-[datetime]'1970-01-01').TotalSeconds);n=$i.Length}}};"
            "$o|ConvertTo-Json -Compress")


def remote_stats(run=subprocess.run, timeout=25):
    """Return {(local_acct, file): (mtime_epoch, size)} or raise RuntimeError."""
    cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", config.G1_HOST,
           "powershell -NoProfile -EncodedCommand " + encoded(remote_stat_script())]
    r = run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"G1 stat failed (exit {r.returncode}): {r.stderr.strip()[:200]}")
    rows = json.loads(r.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    rev = {v: k for k, v in config.ACCOUNTS.items()}
    out = {}
    for row in rows:
        parts = row["p"].replace("\\", "/").split("/")
        acct = parts[parts.index("Account") + 1]
        out[(rev[acct], parts[-1][:-4])] = (int(row["t"]), int(row["n"]))
    return out


def changed_files(data_dir, stats):
    """Files whose G1 time or size differs from the local copy: {(acct, file): (mtime, size)}."""
    out = {}
    for (acct, f), st in stats.items():
        p = local_path(data_dir, acct, f)
        have = os.stat(p) if os.path.exists(p) else None
        if not have or int(have.st_mtime) != st[0] or have.st_size != st[1]:
            out[(acct, f)] = st
    return out


def watch_once(data_dir=config.DATA_DIR, run=subprocess.run, sleep=None, settle=15):
    """One watch step: read the G1 file times. If a file changed, wait until it is stable, then copy.

    The game writes the files at /reload or logout. A second look after `settle` seconds makes sure
    that the game finished the write before the copy starts. Returns the fetch status, or None.
    """
    import time
    sleep = sleep or time.sleep
    try:
        first = changed_files(data_dir, remote_stats(run))
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None          # G1 asleep: try again at the next step
    if not first:
        return None
    sleep(settle)
    try:
        second = changed_files(data_dir, remote_stats(run))
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None
    if any(second.get(k) != v for k, v in first.items()):
        return None          # still being written: the next step copies it
    return fetch(data_dir, run)


def local_path(data_dir, acct, f):
    return os.path.join(data_dir, "current", acct, f + ".lua")


def fetch(data_dir=config.DATA_DIR, run=subprocess.run, now=None):
    """Copy changed files. Return the status dict (also written to fetch-status.json)."""
    now = now or int(dt.datetime.now().timestamp())
    status_path = os.path.join(data_dir, STATUS)
    try:
        status = _read_json(status_path)
    except (OSError, ValueError):
        status = {"accounts": {}}
    status["last_attempt"] = now
    try:
        stats = remote_stats(run)
        status["g1"] = "ok"
        status.pop("g1_error", None)
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        status["g1"] = "unreachable"
        status["g1_error"] = str(e)[:300]
        _write(status_path, status)
        return status
    copied = []
    for acct, g1_acct in config.ACCOUNTS.items():
        a = status["accounts"].setdefault(acct, {"files": {}})
        for f in config.FILES:
            dest = local_path(data_dir, acct, f)
            st = stats.get((acct, f))
            if st is None:
                a["files"].setdefault(f, {})["error"] = "missing on G1"
                continue
            have = os.stat(dest) if os.path.exists(dest) else None
            if have and int(have.st_mtime) == st[0] and have.st_size == st[1]:
                a["files"].setdefault(f, {}).pop("error", None)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".part"
            src = f"{config.G1_HOST}:/{config.G1_SV_DIR.format(acct=g1_acct)}/{f}.lua"
            r = run(["scp", "-p", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", src, tmp],
                    capture_output=True, text=True, timeout=300)
            if r.returncode != 0 or not os.path.exists(tmp):
                a["files"].setdefault(f, {})["error"] = f"scp exit {r.returncode}: {r.stderr.strip()[:200]}"
                continue
            os.replace(tmp, dest)
            a["files"][f] = {"copied_at": now, "mtime": int(os.stat(dest).st_mtime), "size": os.stat(dest).st_size}
            copied.append(f"{acct}/{f}")
    status["last_copied"] = copied
    _write(status_path, status)
    return status


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def seed(src_dir, data_dir=config.DATA_DIR):
    """Copy an older local copy (layout <src>/<acct>/<File>.lua) when no current copy exists."""
    for acct in config.ACCOUNTS:
        for f in config.FILES:
            s, d = os.path.join(src_dir, acct, f + ".lua"), local_path(data_dir, acct, f)
            if os.path.exists(s) and not os.path.exists(d):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)


if __name__ == "__main__":
    s = fetch()
    print(json.dumps({k: s.get(k) for k in ("g1", "g1_error", "last_copied")}, indent=1))
