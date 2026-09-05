#!/usr/bin/env python3
"""Tests for the 95.py path handling in manager_82.py.

Scenario 1  prepare()/sync_selfbot() must not destroy the shared /app/95.py
Scenario 2  a container already damaged by the old build (circular symlink)
            must self-heal from the image's pristine reference copy
"""
import os, shutil, subprocess, sys, tempfile

ROOT = os.path.join(tempfile.gettempdir(), "jafj_selfbot_path_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = os.environ.get(
    "REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INNER1 = """
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["PORT"] = "8123"
os.environ["DATA_DIR"] = os.environ["JAFJ_DATA"]
import manager_82 as M

sup = M.Supervisor(M.Config(), M.DB())
for name in ("write_session", "write_creds", "write_ai",
             "write_defaults", "write_limits"):
    setattr(sup, name, lambda *a, **k: None)   # skip the telethon parts

UID = 111
app95 = os.path.join(os.environ["JAFJ_APP"], "95.py")

def readable(p):
    try:
        open(p, "rb").read(1)
        return True
    except Exception as e:
        return "NO(%s)" % type(e).__name__

def state(tag):
    dst = os.path.join(sup.folder(UID), "95.py")
    print("STATE %s src_isfile=%s src_symlink=%s src_readable=%s client_copy=%s"
          % (tag, os.path.isfile(app95), os.path.islink(app95), readable(app95),
             os.path.isfile(dst)))

print("SELFBOT=%r" % M.SELFBOT)
state("before_prepare")
sup.prepare(UID, "1" * 40, "09120000000")
state("after_prepare")
ok, err = sup.sync_selfbot(UID)
print("SYNC ok=%s err=%s" % (ok, err))
"""

INNER2 = """
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["PORT"] = "8124"
os.environ["DATA_DIR"] = os.environ["JAFJ_DATA"]
import manager_82 as M
p = M.SELFBOT
print("HEAL isfile=%s big=%s path_ok=%s"
      % (os.path.isfile(p), os.path.getsize(p) > 100000,
         p == os.path.join(os.environ["JAFJ_APP"], "95.py")))
"""


def reset():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(APP)
    for f in ("manager_82.py", "95.py"):
        shutil.copy2(os.path.join(SRC_REPO, f), APP)


def run(inner, **extra_env):
    env = dict(os.environ, JAFJ_DATA=DATA, JAFJ_APP=APP)
    env.update(extra_env)
    p = subprocess.run([sys.executable, "-c", inner], cwd=APP, env=env,
                       capture_output=True, text=True)
    return p.stdout + p.stderr


def scenario1():
    print("--- 1: prepare() must not destroy the shared 95.py ---")
    reset()
    out = run(INNER1)
    keep = [l for l in out.splitlines()
            if l.startswith(("SELFBOT=", "STATE ", "SYNC "))]
    print("\n".join(keep) or out[-2000:])
    txt = "\n".join(keep)
    good = ("src_isfile=True src_symlink=False src_readable=True" in txt
            and "client_copy=True" in txt and "SYNC ok=True" in txt)
    print("scenario1:", "PASS" if good else "FAIL")
    return good


def scenario2():
    print("--- 2: self-heal a 95.py damaged into a circular symlink ---")
    reset()
    ref = os.path.join(ROOT, "ref", "95.py")
    os.makedirs(os.path.dirname(ref))
    shutil.copy2(os.path.join(SRC_REPO, "95.py"), ref)
    app95 = os.path.join(APP, "95.py")
    os.remove(app95)
    os.symlink(app95, app95)                       # damage done by the old build
    out = run(INNER2, JAFJ_SELFBOT_REF=ref)
    keep = [l for l in out.splitlines() if l.startswith(("HEAL ", "  ♻"))]
    print("\n".join(keep) or out[-2000:])
    good = "HEAL isfile=True big=True path_ok=True" in out
    print("scenario2:", "PASS" if good else "FAIL")
    return good



INNER3 = """
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["PORT"] = "8125"
os.environ["DATA_DIR"] = os.environ["JAFJ_DATA"]
import manager_82 as M
sup = M.Supervisor(M.Config(), M.DB())
for n in ("write_session", "write_creds", "write_ai", "write_defaults", "write_limits"):
    setattr(sup, n, lambda *a, **k: None)
app95 = os.path.join(os.environ["JAFJ_APP"], "95.py")
os.remove(app95); os.symlink(app95, app95)     # damaged while running
ok, err = sup.sync_selfbot(222)
print("RUNTIME ok=%s err=%s healed=%s" % (ok, err, os.path.isfile(app95)))
"""


def scenario3():
    print("--- 3: sync must heal damage without restarting the manager ---")
    reset()
    ref = os.path.join(ROOT, "ref", "95.py")
    os.makedirs(os.path.dirname(ref))
    shutil.copy2(os.path.join(SRC_REPO, "95.py"), ref)
    out = run(INNER3, JAFJ_SELFBOT_REF=ref)
    keep = [l for l in out.splitlines() if l.startswith(("RUNTIME ", "  \u267b"))]
    print("\n".join(keep) or out[-1500:])
    good = "RUNTIME ok=True" in out and "healed=True" in out
    print("scenario3:", "PASS" if good else "FAIL")
    return good


if __name__ == "__main__":
    ok = scenario1() and scenario2() and scenario3()
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
