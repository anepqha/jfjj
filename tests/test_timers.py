#!/usr/bin/env python3
"""Timer configuration: defaults, command parsing, and Settings migration.

The exchange module gained three new random-delay keys:

  * ``come_min_sec`` / ``come_max_sec``     (پیام «بیا» بعد از جوین)
  * ``response_min_sec`` / ``response_max_sec`` (پاسخ موفق بعد از Join)
  * ``reply_min_sec`` / ``reply_max_sec``   (پاسخ مستقیم رویداد)

The defaults are 34/35, 11/48, 5/18 (seconds). The old scalar keys
(``come_delay_sec``, ``response_delay_sec``) remain readable for
backward compatibility.

The command surface is:

  * ``تبادل زمان بیا <N>``            → set fixed ``come_min=come_max=N``
  * ``تبادل زمان بیا <min> <max>``   → set the whole range
  * ``تبادل زمان پاسخ <N>``          → set fixed ``response_min=response_max=N``
  * ``تبادل زمان پاسخ <min> <max>`` → set the whole range
  * ``تبادل زمان جواب <min> <max>``  → set reply range
  * ``تبادل زمان جواب <N>``          → set fixed reply range

This test exercises:
  1. ``Settings.__init__`` exposes the new keys with the documented
     defaults.
  2. ``Settings`` migrates an old settings.json (only ``come_delay_sec`` /
     ``response_delay_sec``) without losing the new keys.
  3. ``Engine.exchange_cmd`` parses the timer commands correctly,
     including the "single value" form, the "min max" form, and the
     bad-format fallbacks.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_timers_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = REPO


DRIVER = r"""
import os, sys, json, importlib.util
sys.path.insert(0, os.getcwd())

spec = importlib.util.spec_from_file_location("m95", "95.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


# ── 1. Defaults present in Settings ──
eng = m.Engine()
x = eng.ex_cfg()
must_have = {
    "come_min_sec": 34,
    "come_max_sec": 35,
    "response_min_sec": 11,
    "response_max_sec": 48,
    "reply_min_sec": 5,
    "reply_max_sec": 18,
}
for k, v in must_have.items():
    got = x.get(k)
    if got != v:
        print(f"DEFAULT FAIL {k}={got!r} want {v!r}")
        sys.exit(1)
    print(f"DEFAULT {k}={got}")
print("OK defaults present and in the documented ranges")


# ── 2. Old settings.json migrates cleanly to the new keys ──
old_settings_path = "jafj_settings.json"
# Start with a *fresh* copy of the old config — the only exchange keys
# are the legacy scalars.
old = json.loads(json.dumps(m.DEFAULTS))
old["exchange"].pop("come_min_sec", None)
old["exchange"].pop("come_max_sec", None)
old["exchange"].pop("response_min_sec", None)
old["exchange"].pop("response_max_sec", None)
old["exchange"].pop("reply_min_sec", None)
old["exchange"].pop("reply_max_sec", None)
old["exchange"]["come_delay_sec"] = 0
old["exchange"]["response_delay_sec"] = 7
with open(old_settings_path, "w", encoding="utf-8") as f:
    json.dump(old, f)
fresh = m.Settings()
fx = fresh["exchange"]
print("MIG come_min_sec:", fx.get("come_min_sec"))
print("MIG come_max_sec:", fx.get("come_max_sec"))
print("MIG response_min_sec:", fx.get("response_min_sec"))
print("MIG response_max_sec:", fx.get("response_max_sec"))
print("MIG reply_min_sec:", fx.get("reply_min_sec"))
print("MIG reply_max_sec:", fx.get("reply_max_sec"))
print("MIG old come_delay_sec still readable:", fx.get("come_delay_sec"))
print("MIG old response_delay_sec still readable:", fx.get("response_delay_sec"))

# All the new keys must end up populated with the documented defaults.
for k, v in must_have.items():
    if fx.get(k) != v:
        print(f"MIGRATION FAIL {k}={fx.get(k)!r} want {v!r}")
        sys.exit(1)
# The old scalar must still be present.
if fx.get("come_delay_sec") != 0 or fx.get("response_delay_sec") != 7:
    print("MIGRATION FAIL old scalars lost")
    sys.exit(1)
print("OK migration: old scalars preserved, new keys filled with defaults")


# ── 3. exchange_cmd: set a fixed come time ──
# Clean up before the next scenario so it operates on defaults.
fresh = m.Settings()  # re-load to restore defaults
eng2 = m.Engine()
eng2.st = fresh
eng2.db = m.DB()  # new in-memory db
out = eng2.exchange_cmd("زمان بیا ۴۰")
print("CMD زمان بیا ۴۰:", out)
if "40" not in out:
    print("FAIL command did not echo 40")
    sys.exit(1)
x2 = eng2.ex_cfg()
if x2.get("come_min_sec") != 40 or x2.get("come_max_sec") != 40:
    print(f"FAIL come min/max not 40/40: {x2.get('come_min_sec')}/{x2.get('come_max_sec')}")
    sys.exit(1)
print("OK زمان بیا ۴۰ → come_min=come_max=40")


# ── 4. exchange_cmd: set a come *range* ──
out = eng2.exchange_cmd("زمان بیا ۳۴ ۳۵")
print("CMD زمان بیا ۳۴ ۳۵:", out)
x2 = eng2.ex_cfg()
if x2.get("come_min_sec") != 34 or x2.get("come_max_sec") != 35:
    print(f"FAIL come range: {x2.get('come_min_sec')}-{x2.get('come_max_sec')}")
    sys.exit(1)
print("OK زمان بیا ۳۴ ۳۵ → come_min=34, come_max=35")


# ── 5. exchange_cmd: set a response range ──
out = eng2.exchange_cmd("زمان پاسخ ۱۱ ۴۸")
print("CMD زمان پاسخ ۱۱ ۴۸:", out)
x2 = eng2.ex_cfg()
if x2.get("response_min_sec") != 11 or x2.get("response_max_sec") != 48:
    print(f"FAIL response range: {x2.get('response_min_sec')}-{x2.get('response_max_sec')}")
    sys.exit(1)
print("OK زمان پاسخ ۱۱ ۴۸ → response_min=11, response_max=48")


# ── 6. exchange_cmd: set a reply range ──
out = eng2.exchange_cmd("زمان جواب ۵ ۱۸")
print("CMD زمان جواب ۵ ۱۸:", out)
x2 = eng2.ex_cfg()
if x2.get("reply_min_sec") != 5 or x2.get("reply_max_sec") != 18:
    print(f"FAIL reply range: {x2.get('reply_min_sec')}-{x2.get('reply_max_sec')}")
    sys.exit(1)
print("OK زمان جواب ۵ ۱۸ → reply_min=5, reply_max=18")


# ── 7. exchange_cmd: fixed reply ──
out = eng2.exchange_cmd("زمان جواب ۱۰")
print("CMD زمان جواب ۱۰:", out)
x2 = eng2.ex_cfg()
if x2.get("reply_min_sec") != 10 or x2.get("reply_max_sec") != 10:
    print(f"FAIL reply fixed: {x2.get('reply_min_sec')}/{x2.get('reply_max_sec')}")
    sys.exit(1)
print("OK زمان جواب ۱۰ → reply_min=reply_max=10")


# ── 8. exchange_cmd: bad format gives a friendly error ──
out = eng2.exchange_cmd("زمان بیا الف")
print("CMD زمان بیا الف:", out)
if "فرمت" not in out and "عدد" not in out:
    print(f"FAIL bad format: {out!r}")
    sys.exit(1)
print("OK bad format gives a clear error")


# ── 9. Display path: with min<max, message should mention both ──
eng2.exchange_cmd("زمان بیا ۳۴ ۳۵")
display = eng2.exchange_cmd("زمان بیا")
print("DISPLAY زمان بیا:", display)
if "34" not in display or "35" not in display:
    print(f"FAIL display missing 34/35: {display!r}")
    sys.exit(1)
print("OK display echoes both 34 and 35 when min<max")

print("DONE PASS")
"""


def reset():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(APP, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    for f in ("95.py", "manager_82.py"):
        shutil.copy2(os.path.join(SRC_REPO, f), APP)


def run():
    env = dict(os.environ)
    env["DATA_DIR"] = DATA
    env["PORT"] = "8203"
    env["JAFJ_PORT"] = "8203"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=60)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- timers: defaults + migration + command surface ---")
    reset()
    out, err, rc = run()
    if err.strip():
        print("--- driver stderr (tail) ---")
        print(err[-2000:])
    print("--- driver output ---")
    for line in out.splitlines():
        if line.startswith(("DEFAULT", "MIG", "OK", "CMD", "DISPLAY",
                            "FAIL", "DONE")):
            print(line)
    if "DONE PASS" in out and "FAIL" not in out:
        print("ALL: PASS")
        return True
    print("ALL: FAIL")
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
