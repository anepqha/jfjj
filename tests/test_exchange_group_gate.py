#!/usr/bin/env python3
"""Anti-spam gate for exchange requests in groups.

The old handler used to react to "جوین شدم" in any group that happened
to be in the watch list (or even outside it when the message mentioned
a channel link). That turned into spam — the bot would reply "not
joined" to messages between two strangers.

The gate now requires the incoming group message to be either a *reply
to a message of this self-account* (``replied_to_me``) or a *mention*
of this account (``event.mentioned``). PV behaviour is unchanged.

This test extracts the gate block from the *real* 95.py source and
re-runs it against fake events in a subprocess. Five scenarios:

  s1: group + "جوین شدم" + no reply, no mention            → blocked
  s2: group + "جوین شدم" replying to one of OUR messages    → pass
  s3: group + "جوین شدم" mentioning our username            → pass
  s4: PV (private) "جوین شدم"                                → pass
  s5: group + public channel link, no reply/mention         → blocked
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_exchange_group_gate_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = REPO


# The driver runs as `python -c <DRIVER>` in the APP directory. It
# 1) imports 95.py
# 2) extracts the gate block (a small if/else) by string-locating it
# 3) compiles the gate as a top-level Python function, then runs five
#    scenarios and prints one OUT line per scenario
DRIVER = r"""
import os, sys, re, importlib.util
sys.path.insert(0, os.getcwd())

spec = importlib.util.spec_from_file_location("m95", "95.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

src = open("95.py", encoding="utf-8").read()
i = src.find("# کجاها گوش بده")
if i < 0:
    print("GATE_NOT_FOUND")
    sys.exit(1)
end_marker = 'if not replied_to_me and not getattr(event, "mentioned", False):\n                return'
j = src.find(end_marker, i)
if j < 0:
    print("GATE_END_NOT_FOUND")
    sys.exit(1)
j += len(end_marker)
gate = src[i:j]

# Strip the leading "# کجاها گوش بده\n" comment and the blank line
# after it, then dedent one level (8 spaces) and translate the inner
# `return` to `return True` (gate-blocked).
gate_lines = gate.splitlines()
while gate_lines and (gate_lines[0].lstrip().startswith("# کجاها")
                      or gate_lines[0].strip() == ""):
    gate_lines.pop(0)
# Remove trailing blank lines.
while gate_lines and gate_lines[-1].strip() == "":
    gate_lines.pop()
# Dedent: every line starts with 8 spaces of method body indent; remove
# exactly those 8 spaces.
gate_lines = [ln[8:] if ln.startswith("        ") else ln for ln in gate_lines]
# The bare `return` becomes `return True`.
gate_lines = [ln.replace("return", "return True") if ln.strip() == "return"
              else ln for ln in gate_lines]
body = "\n".join("    " + ln for ln in gate_lines)
func_src = "def gate(event, replied_to_me):\n" + body + "\n    return False\n"
scope = {}
exec(func_src, scope)
gate_fn = scope["gate"]


class E:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


scenarios = [
    ("s1", dict(is_private=False, mentioned=False, replied_to_me=False),  True),
    ("s2", dict(is_private=False, mentioned=False, replied_to_me=True),   False),
    ("s3", dict(is_private=False, mentioned=True,  replied_to_me=False),  False),
    ("s4", dict(is_private=True,  mentioned=False, replied_to_me=False),  False),
    ("s5", dict(is_private=False, mentioned=False, replied_to_me=False),  True),
]

all_ok = True
for name, kw, expect_blocked in scenarios:
    ev = E(**kw)
    try:
        blocked = bool(gate_fn(ev, kw["replied_to_me"]))
    except Exception as e:
        print(f"OUT {name} = ERROR {type(e).__name__}: {e}")
        all_ok = False
        continue
    good = (blocked == expect_blocked)
    print(f"OUT {name}_blocked = {blocked}  expect={expect_blocked}  {'PASS' if good else 'FAIL'}")
    if not good:
        all_ok = False
print(f"DONE {'PASS' if all_ok else 'FAIL'}")
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
    env["PORT"] = "8201"
    env["JAFJ_PORT"] = "8201"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=60)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- exchange_group_gate: 5 scenarios ---")
    reset()
    out, err, rc = run()
    if err.strip():
        print("--- driver stderr (tail) ---")
        print(err[-2000:])
    lines = out.splitlines()
    print("--- driver output ---")
    for line in lines:
        if line.startswith("OUT ") or line.startswith("DONE"):
            print(line)

    if "GATE_NOT_FOUND" in out or "GATE_END_NOT_FOUND" in out:
        print("RUN FAIL - gate block not located in 95.py")
        return False

    failed = [l for l in lines if " FAIL" in l]
    done = next((l for l in lines if l.startswith("DONE")), "")
    if "DONE PASS" in done and not failed:
        print("ALL: PASS")
        return True
    print("ALL: FAIL")
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
