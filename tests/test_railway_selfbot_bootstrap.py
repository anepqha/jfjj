#!/usr/bin/env python3
"""Railway entrypoint contract.

The entrypoint must guarantee two things, whatever the Volume situation is:

  1. the code that runs is the one baked into the image (/opt/jafj), never a
     stale copy left behind in a Volume mounted over /app — that stale copy is
     exactly what kept answering "فایل 95.py پیدا نشد" after every redeploy;
  2. a manager that exits does not take the container down with it, so Railway
     does not restart-loop.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(os.environ.get("REPO", Path(__file__).resolve().parents[1]))
ROOT = Path(tempfile.gettempdir()) / "jafj_railway_entrypoint_test"

# stand-in managers: the real one would try to reach Telegram
FRESH_MANAGER = (
    "import os, sys, pathlib\n"
    "print('MANAGER_BUILD fresh', flush=True)\n"
    "print('CODE_DIR', pathlib.Path(sys.argv[0]).resolve().parent, flush=True)\n"
    "print('DATA_DIR', os.environ.get('DATA_DIR'), flush=True)\n"
    "print('CWD', os.getcwd(), flush=True)\n"
    "print('SELF_ISFILE', os.path.isfile(os.path.join(os.environ['JAFJ_IMAGE_DIR'], '95.py')), flush=True)\n"
)
STALE_MANAGER = "print('MANAGER_BUILD stale-volume', flush=True)\n"


def build(volume_files, image_self=True, image_manager=True):
    """Lay out a fake container: image dir + Volume dir."""
    shutil.rmtree(ROOT, ignore_errors=True)
    image = ROOT / "opt" / "jafj"
    app = ROOT / "app"
    image.mkdir(parents=True)
    app.mkdir(parents=True)
    (image / "railway-start.sh").write_text(
        (REPO / "railway-start.sh").read_text(encoding="utf-8"), encoding="utf-8")
    if image_manager:
        (image / "manager_82.py").write_text(FRESH_MANAGER, encoding="utf-8")
    if image_self:
        (image / "95.py").write_text(
            (REPO / "95.py").read_text(encoding="utf-8"), encoding="utf-8")
    for name, kind in volume_files.items():
        target = app / name
        if kind == "symlink-loop":
            target.symlink_to(target.name)      # the damage the old build left
        elif kind == "stale-manager":
            target.write_text(STALE_MANAGER, encoding="utf-8")
        else:
            target.write_text(str(kind), encoding="utf-8")
    return image, app


def run(image, app, extra_env=None, timeout=60):
    env = dict(os.environ, JAFJ_IMAGE_DIR=str(image), JAFJ_APP_DIR=str(app),
               JAFJ_NAP_BASE="0")
    env.pop("DATA_DIR", None)
    env.update(extra_env or {})
    return subprocess.run(["sh", str(image / "railway-start.sh")], env=env,
                          text=True, capture_output=True, timeout=timeout)


def scenario_stale_volume():
    """A Volume over /app holds the OLD manager + a broken 95.py."""
    print("--- 1: stale Volume must not be able to run old code ---")
    image, app = build({"manager_82.py": "stale-manager",
                        "95.py": "symlink-loop"})
    r = run(image, app)
    out = r.stdout + r.stderr
    print("\n".join(out.strip().splitlines()[-8:]))
    good = (r.returncode == 0
            and "MANAGER_BUILD fresh" in out
            and "MANAGER_BUILD stale-volume" not in out
            and f"CODE_DIR {image}" in out
            and f"DATA_DIR {app}" in out          # data stays on the Volume
            and "SELF_ISFILE True" in out
            and "NOT executed" in out)            # the stale copy is called out
    print("scenario1:", "PASS" if good else "FAIL")
    return good


def scenario_empty_volume():
    """Volume mounted over /app hides every image file (empty dir)."""
    print("--- 2: empty Volume must not exit(1) into a restart loop ---")
    image, app = build({})
    r = run(image, app)
    out = r.stdout + r.stderr
    print("\n".join(out.strip().splitlines()[-6:]))
    good = (r.returncode == 0 and "MANAGER_BUILD fresh" in out
            and f"DATA_DIR {app}" in out)
    print("scenario2:", "PASS" if good else "FAIL")
    return good


def scenario_no_volume():
    """Plain container: /app is just the image workdir."""
    print("--- 3: no Volume still boots from the image copy ---")
    image, app = build({"manager_config.json": "{}"})
    r = run(image, app)
    out = r.stdout + r.stderr
    print("\n".join(out.strip().splitlines()[-6:]))
    good = (r.returncode == 0 and "MANAGER_BUILD fresh" in out
            and f"CODE_DIR {image}" in out and f"DATA_DIR {app}" in out)
    print("scenario3:", "PASS" if good else "FAIL")
    return good


def scenario_supervisor_restarts():
    """Manager crashes twice, then succeeds: container must survive."""
    print("--- 4: crashed manager is restarted in place ---")
    image, app = build({})
    (image / "manager_82.py").write_text(
        "import os, sys\n"
        f"p = os.path.join({str(app)!r}, 'runs')\n"
        "n = int(open(p).read()) if os.path.exists(p) else 0\n"
        "open(p, 'w').write(str(n + 1))\n"
        "print('RUN', n + 1, flush=True)\n"
        "sys.exit(0 if n >= 2 else 1)\n", encoding="utf-8")
    r = run(image, app, timeout=120)
    out = r.stdout + r.stderr
    print("\n".join(out.strip().splitlines()[-8:]))
    good = (r.returncode == 0 and "RUN 3" in out
            and out.count("restarting in") == 2
            and "exited cleanly" in out)
    print("scenario4:", "PASS" if good else "FAIL")
    return good


def scenario_supervisor_gives_up():
    """A manager that never boots must eventually be handed back to Railway."""
    print("--- 5: endless instant crashes are given up ---")
    image, app = build({})
    (image / "manager_82.py").write_text(
        "import sys; print('BOOM', flush=True); sys.exit(3)\n", encoding="utf-8")
    r = run(image, app, {"JAFJ_MAX_CRASHES": "2"}, timeout=120)
    out = r.stdout + r.stderr
    print("\n".join(out.strip().splitlines()[-4:]))
    good = r.returncode == 3 and out.count("BOOM") == 2 and "giving up" in out
    print("scenario5:", "PASS" if good else "FAIL")
    return good


def scenario_sigterm_forwarded():
    """Railway's graceful stop must reach the manager, not just the shell."""
    print("--- 6: SIGTERM is forwarded to the manager ---")
    image, app = build({})
    (image / "manager_82.py").write_text(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *a: (print('GOT_TERM', flush=True),"
        " sys.exit(0)))\n"
        "print('READY', flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8")
    env = dict(os.environ, JAFJ_IMAGE_DIR=str(image), JAFJ_APP_DIR=str(app))
    env.pop("DATA_DIR", None)
    proc = subprocess.Popen(["sh", str(image / "railway-start.sh")], env=env,
                            text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    line = ""
    while time.time() < deadline:
        line = proc.stdout.readline()
        if "READY" in line:
            break
    proc.send_signal(signal.SIGTERM)
    try:
        rest = proc.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        rest = "TIMEOUT"
    out = line + rest
    print("\n".join(out.strip().splitlines()[-5:]))
    good = "GOT_TERM" in out and "TIMEOUT" not in out and proc.returncode == 0
    print("scenario6:", "PASS" if good else "FAIL")
    return good


def scenario_config():
    print("--- 7: Dockerfile / railway.json point at the entrypoint ---")
    docker = (REPO / "Dockerfile").read_text(encoding="utf-8")
    railway = (REPO / "railway.json").read_text(encoding="utf-8")
    good = ("cp /app/manager_82.py /opt/jafj/manager_82.py" in docker
            and "cp /app/95.py /opt/jafj/95.py" in docker
            and 'CMD ["sh", "/opt/jafj/railway-start.sh"]' in docker
            and '"startCommand": "sh /opt/jafj/railway-start.sh"' in railway)
    print("scenario7:", "PASS" if good else "FAIL")
    return good


if __name__ == "__main__":
    results = [scenario_stale_volume(), scenario_empty_volume(),
               scenario_no_volume(), scenario_supervisor_restarts(),
               scenario_supervisor_gives_up(), scenario_sigterm_forwarded(),
               scenario_config()]
    ok = all(results)
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
