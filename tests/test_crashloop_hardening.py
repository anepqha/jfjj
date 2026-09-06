#!/usr/bin/env python3
"""Crash-loop hardening inside manager_82.py.

Three ways the manager used to exit and take the Railway container with it:

  1. a bot.lock left in the Volume pointed at a PID that exists in the new
     container -> "یک نسخه در حال اجراست" -> exit(1) -> restart -> loop;
  2. a Telegram login that failed (FloodWait after repeated restarts) ->
     exit(1) -> restart -> more FloodWait -> loop;
  3. the pristine reference copy being overwritten by a stale runtime copy,
     which destroyed the only way to rebuild 95.py.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(tempfile.gettempdir(), "jafj_crashloop_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
REF = os.path.join(ROOT, "ref")
TMP = os.path.join(ROOT, "tmp")

PORT = [8210]


def reset():
    shutil.rmtree(ROOT, ignore_errors=True)
    for d in (APP, DATA, REF, TMP):
        os.makedirs(d)
    for f in ("manager_82.py", "95.py"):
        shutil.copy2(os.path.join(REPO, f), APP)


def run(inner, argv=(), extra_env=None, timeout=90):
    PORT[0] += 1
    env = dict(os.environ, JAFJ_DATA=DATA, JAFJ_REF=os.path.join(REF, "95.py"),
               JAFJ_TMP=TMP, JAFJ_PORT=str(PORT[0]), DATA_DIR=DATA,
               PORT=str(PORT[0]),
               # the module reads SELFBOT_REF from the environment at import
               JAFJ_SELFBOT_REF=os.path.join(REF, "95.py"))
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "JAFJ_HOSTED",
              "JAFJ_LOGIN_RETRY"):
        env.pop(k, None)
    env.update(extra_env or {})
    p = subprocess.run([sys.executable, "-c", inner, *argv], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=timeout)
    return p.stdout + p.stderr


INNER_LOCK = r'''
import os, subprocess, sys, time
sys.path.insert(0, os.getcwd())
import manager_82 as M

mode = sys.argv[1]
if mode == "stale":
    # a PID that is alive in this container but is NOT a manager: exactly what
    # a bot.lock left over in the Volume points at after a redeploy.
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(0.4)
    with open(M.LOCK_FILE, "w") as f:
        f.write(str(p.pid))
    alive = M.pid_alive(p.pid)
    ok, other = M.acquire_lock()
    print("STALE alive=%s pid_is_manager=%s ok=%s owner_is_me=%s"
          % (alive, M.pid_is_manager(p.pid), ok, other == os.getpid()))
    p.terminate()
elif mode == "real":
    # a genuine second manager instance must still be refused
    helper = os.path.join(os.environ["JAFJ_TMP"], "manager_82.py")
    with open(helper, "w") as f:
        f.write("import time\ntime.sleep(30)\n")
    p = subprocess.Popen([sys.executable, helper])
    time.sleep(0.6)
    with open(M.LOCK_FILE, "w") as f:
        f.write(str(p.pid))
    ok, other = M.acquire_lock()
    print("REAL alive=%s pid_is_manager=%s ok=%s owner=%s"
          % (M.pid_alive(p.pid), M.pid_is_manager(p.pid), ok, other == p.pid))
    p.terminate()
'''


def scenario_lock():
    print("--- 1: stale bot.lock must not block boot ---")
    reset()
    out = run(INNER_LOCK, ("stale",))
    keep = [l for l in out.splitlines() if l.startswith(("STALE", "REAL"))]
    print("\n".join(keep) or out[-1500:])
    good = "STALE alive=True pid_is_manager=False ok=True owner_is_me=True" in out
    print("scenario1:", "PASS" if good else "FAIL")

    print("--- 2: a real second manager is still refused ---")
    reset()
    out = run(INNER_LOCK, ("real",))
    keep = [l for l in out.splitlines() if l.startswith(("STALE", "REAL"))]
    print("\n".join(keep) or out[-1500:])
    good2 = "REAL alive=True pid_is_manager=True ok=False owner=True" in out
    print("scenario2:", "PASS" if good2 else "FAIL")
    return good and good2


INNER_REF = r'''
import os, sys, time
sys.path.insert(0, os.getcwd())
import manager_82 as M

ref = os.environ["JAFJ_REF"]
src = os.path.join(os.getcwd(), "95.py")
mode = sys.argv[1]


def write(path, text, mtime=None):
    with open(path, "w") as f:
        f.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


now = time.time()
if mode == "protect":
    # pristine image copy is NEWER than the stale runtime copy
    write(src, "STALE" * 100, now - 5000)
    write(ref, "PRISTINE" * 100, now - 10)
    changed = M.ensure_selfbot_ref(src)
    print("PROTECT changed=%s ref_intact=%s"
          % (changed, open(ref).read().startswith("PRISTINE")))
elif mode == "create":
    write(src, "RUNTIME" * 100, now - 10)
    if os.path.exists(ref):
        os.remove(ref)
    created = M.ensure_selfbot_ref(src)
    print("CREATE created=%s same=%s"
          % (created, os.path.isfile(ref) and open(ref).read() == open(src).read()))
elif mode == "refresh":
    write(src, "OLD" * 100, now - 5000)
    write(ref, "FRESH" * 100, now - 10)
    M.SELFBOT = src
    did = M.ensure_selfbot_current(src)
    print("REFRESH did=%s content=%s" % (did, open(src).read()[:5]))
elif mode == "keepnewer":
    write(src, "NEWER" * 100, now - 10)
    write(ref, "OLDER" * 100, now - 5000)
    M.SELFBOT = src
    did = M.ensure_selfbot_current(src)
    print("KEEP did=%s content=%s" % (did, open(src).read()[:5]))
'''


def scenario_selfbot_ref():
    print("--- 3: pristine reference is not clobbered by a stale copy ---")
    reset()
    out = run(INNER_REF, ("protect",))
    keep = [l for l in out.splitlines() if l.startswith(("PROTECT", "CREATE",
                                                         "REFRESH", "KEEP"))]
    print("\n".join(keep) or out[-1500:])
    a = "PROTECT changed=False ref_intact=True" in out

    print("--- 4: missing reference is created from the runtime copy ---")
    reset()
    out = run(INNER_REF, ("create",))
    keep = [l for l in out.splitlines() if l.startswith(("PROTECT", "CREATE"))]
    print("\n".join(keep) or out[-1500:])
    b = "CREATE created=True same=True" in out

    print("--- 5: stale runtime self is refreshed from the image copy ---")
    reset()
    out = run(INNER_REF, ("refresh",))
    keep = [l for l in out.splitlines() if l.startswith("REFRESH")]
    print("\n".join(keep) or out[-1500:])
    c = "REFRESH did=True content=FRESH" in out

    print("--- 6: a newer runtime self is left alone ---")
    reset()
    out = run(INNER_REF, ("keepnewer",))
    keep = [l for l in out.splitlines() if l.startswith("KEEP")]
    print("\n".join(keep) or out[-1500:])
    d = "KEEP did=False content=NEWER" in out
    for name, ok in (("scenario3", a), ("scenario4", b),
                     ("scenario5", c), ("scenario6", d)):
        print(name + ":", "PASS" if ok else "FAIL")
    return a and b and c and d


INNER_REEXEC = r'''
import os, sys
sys.path.insert(0, os.getcwd())
import manager_82 as M

# pretend this process was started as the stale copy living in the data dir
sys.argv = [os.path.join(os.getcwd(), "manager_82.py")]
print("BEFORE image_dir=%s" % M.image_dir(), flush=True)
changed = M.ensure_fresh_code()
print("AFTER changed=%s (should not be reached when it re-execs)" % changed,
      flush=True)
'''


INNER_LOGIN = r'''
import asyncio, os, sys
sys.path.insert(0, os.getcwd())
import manager_82 as M

# check the real backoff first, then stop the tests from actually sleeping
REAL_WAIT = [M.login_retry_wait(i) for i in (1, 5, 50)]
M.login_retry_wait = lambda n, base=20, cap=300: 0


class Fake:
    def __init__(self, fail_times):
        self.calls = 0
        self.fail = fail_times

    async def login_bot(self, tries=5):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("FloodWaitError: 12 seconds")
        return "client"


async def go():
    print("HOSTED=%s FOREVER=%s WAIT=%s"
          % (M.is_hosted(), M.login_retry_forever(), REAL_WAIT))
    f = Fake(2)
    try:
        client = await M.Manager.login_with_patience(f, tries=1)
        print("PATIENCE calls=%s client=%s" % (f.calls, client))
    except RuntimeError:
        print("PATIENCE raised=True calls=%s" % f.calls)
    if not M.login_retry_forever():
        # off-host the old behaviour must stay: give up so the user sees it
        f2 = Fake(9)
        try:
            await M.Manager.login_with_patience(f2, tries=1)
            print("GIVEUP raised=False")
        except RuntimeError:
            print("GIVEUP raised=True calls=%s" % f2.calls)


asyncio.run(go())
'''


def scenario_reexec():
    print("--- 9: stale runtime copy re-execs from the image copy ---")
    reset()
    fresh_dir = os.path.join(ROOT, "fresh")
    os.makedirs(fresh_dir, exist_ok=True)
    with open(os.path.join(fresh_dir, "manager_82.py"), "w") as f:
        f.write("import os\n"
                "print('REEXEC_OK guard=%s' % os.environ.get('JAFJ_REEXEC_GUARD'))\n")
    out = run(INNER_REEXEC, extra_env={"JAFJ_IMAGE_DIR": fresh_dir})
    keep = [l for l in out.splitlines() if l.startswith(("BEFORE", "AFTER",
                                                         "REEXEC_OK"))]
    print("\n".join(keep) or out[-1500:])
    a = "REEXEC_OK guard=1" in out and "AFTER" not in out

    print("--- 10: the re-exec guard prevents an endless loop ---")
    reset()
    out = run(INNER_REEXEC, extra_env={"JAFJ_IMAGE_DIR": fresh_dir,
                                       "JAFJ_REEXEC_GUARD": "1"})
    keep = [l for l in out.splitlines() if l.startswith(("BEFORE", "AFTER",
                                                         "REEXEC_OK"))]
    print("\n".join(keep) or out[-1500:])
    b = "AFTER changed=False" in out and "REEXEC_OK" not in out
    print("scenario9:", "PASS" if a else "FAIL")
    print("scenario10:", "PASS" if b else "FAIL")
    return a and b


def scenario_login():
    print("--- 7: on Railway a failed login retries instead of exiting ---")
    reset()
    out = run(INNER_LOGIN, extra_env={"RAILWAY_ENVIRONMENT": "production"})
    keep = [l for l in out.splitlines() if l.startswith(("HOSTED", "PATIENCE",
                                                         "GIVEUP"))]
    print("\n".join(keep) or out[-1500:])
    a = ("HOSTED=True FOREVER=True WAIT=[20, 100, 300]" in out
         and "PATIENCE calls=3 client=client" in out
         and "GIVEUP" not in out)

    print("--- 8: off-host (Termux) behaviour is unchanged ---")
    reset()
    out = run(INNER_LOGIN)
    keep = [l for l in out.splitlines() if l.startswith(("HOSTED", "PATIENCE",
                                                         "GIVEUP"))]
    print("\n".join(keep) or out[-1500:])
    b = ("HOSTED=False FOREVER=False" in out
         and "PATIENCE raised=True calls=1" in out
         and "GIVEUP raised=True calls=1" in out)
    print("scenario7:", "PASS" if a else "FAIL")
    print("scenario8:", "PASS" if b else "FAIL")
    return a and b


if __name__ == "__main__":
    ok = (scenario_lock() and scenario_selfbot_ref() and scenario_reexec()
          and scenario_login())
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
