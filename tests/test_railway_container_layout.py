#!/usr/bin/env python3
"""End-to-end: real manager_82.py + real railway-start.sh in a container whose
/app is a stale Railway Volume.

This is the exact setup that produced the user's report:

    🟡 به‌روزرسانی فایل سلف نشد: فایل 95.py پیدا نشد

The Volume held an old manager_82.py and a 95.py damaged into a circular
symlink, so every redeploy kept running the old code. Here the real entrypoint
must boot the real manager from the image copy, resolve the real 95.py, and put
a working self into the client folder — with all data still on the Volume.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ.get("REPO", Path(__file__).resolve().parents[1]))
ROOT = Path(tempfile.gettempdir()) / "jafj_container_layout_test"

DRIVER = '''
import os, sys
code_dir, manager = os.environ["JAFJ_CODE_DIR"], sys.argv[1]
# reproduce `python /opt/jafj/manager_82.py`: argv[0] decides the search dirs
sys.argv = [manager]
sys.path.insert(0, code_dir)
import manager_82 as M

print("IMPORTED_FROM", M.__file__, flush=True)
print("SELFBOT", M.SELFBOT, flush=True)
print("SELFBOT_ISFILE", os.path.isfile(M.SELFBOT), flush=True)
print("SELFBOT_SIZE", os.path.getsize(M.SELFBOT) if os.path.isfile(M.SELFBOT) else 0, flush=True)
print("DATA", os.path.abspath(M.BASE_DIR), flush=True)
print("CLIENTS", os.path.abspath(M.CLIENTS_DIR), flush=True)

sup = M.Supervisor(M.Config(), M.DB())
for name in ("write_session", "write_creds", "write_ai",
             "write_defaults", "write_limits"):
    setattr(sup, name, lambda *a, **k: None)      # no Telegram in tests
ok, err = sup.sync_selfbot(4242)
dst = os.path.join(sup.folder(4242), "95.py")
print("SYNC", ok, repr(err), flush=True)
print("CLIENT_SELF", os.path.isfile(dst),
      os.path.getsize(dst) if os.path.isfile(dst) else 0, flush=True)
ok, err = sup.link_selfbot(4242)
print("LINK", ok, repr(err), flush=True)
'''


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    image = ROOT / "opt" / "jafj"          # immutable part of the image
    app = ROOT / "app"                     # Railway Volume
    image.mkdir(parents=True)
    app.mkdir(parents=True)
    for name in ("manager_82.py", "95.py", "railway-start.sh"):
        shutil.copy2(REPO / name, image / name)
    # what the old deployment left behind in the Volume:
    (app / "manager_82.py").write_text("print('MANAGER_BUILD stale-volume')\n",
                                       encoding="utf-8")
    (app / "95.py").symlink_to("95.py")            # circular symlink
    # real leftover data: a valid (empty) database plus a marker file
    import sqlite3
    sqlite3.connect(str(app / "manager.db")).close()
    (app / "volume-marker.txt").write_text("keep me", encoding="utf-8")

    driver = ROOT / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    wrapper = ROOT / "python-wrapper.sh"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n',
                       encoding="utf-8")
    wrapper.chmod(0o755)

    # PYTHON makes the entrypoint run the driver through the real manager path
    # (`<python> /opt/jafj/manager_82.py`) without dialling Telegram.
    env = dict(os.environ, JAFJ_IMAGE_DIR=str(image), JAFJ_APP_DIR=str(app),
               JAFJ_CODE_DIR=str(image), PORT="8299", PYTHON=str(wrapper),
               JAFJ_SUPERVISE="0")
    env.pop("DATA_DIR", None)
    r = subprocess.run(["sh", str(image / "railway-start.sh")], env=env,
                       text=True, capture_output=True, timeout=120)
    out = r.stdout + r.stderr
    keep = [l for l in out.splitlines()
            if l.startswith(("SELFBOOT", "IMPORTED_FROM", "SELFBOT", "DATA",
                             "CLIENTS", "SYNC", "CLIENT_SELF", "LINK"))]
    print("\n".join(keep) or out[-3000:])

    self_size = (REPO / "95.py").stat().st_size
    checks = {
        "real manager imported from the image, not the Volume":
            f"IMPORTED_FROM {image / 'manager_82.py'}" in out,
        "stale Volume manager never ran":
            "MANAGER_BUILD stale-volume" not in out,
        "95.py resolved and readable":
            "SELFBOT_ISFILE True" in out and f"SELFBOT_SIZE {self_size}" in out,
        "data stays on the Volume":
            f"DATA {app}" in out and f"CLIENTS {app / 'clients'}" in out,
        "self synced into the client folder":
            "SYNC True ''" in out and f"CLIENT_SELF True {self_size}" in out,
        "link_selfbot succeeds": "LINK True ''" in out,
        "existing Volume data untouched":
            (app / "manager.db").exists()
            and (app / "volume-marker.txt").read_text(encoding="utf-8") == "keep me",
        "entrypoint exited cleanly": r.returncode == 0,
    }
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    ok = all(checks.values())
    print("ALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
