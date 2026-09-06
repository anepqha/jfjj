#!/usr/bin/env python3
"""Verify the Railway entrypoint restores 95.py before manager_82.py starts."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "jafj_railway_selfboot_test"
REPO = Path(os.environ.get("REPO", Path(__file__).resolve().parents[1]))
START = REPO / "railway-start.sh"


def reset():
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / "app").mkdir(parents=True)
    (ROOT / "image").mkdir(parents=True)


def run_bootstrap(kind):
    reset()
    app = ROOT / "app"
    image = ROOT / "image"
    clean = image / "95.py"
    clean.write_text("# clean image selfbot\nVERSION = '3.0'\n", encoding="utf-8")

    target = app / "95.py"
    if kind == "broken-link":
        target.symlink_to(target.name)  # Old circular/self link in the Volume.
    else:
        target.write_text("# stale volume selfbot\n", encoding="utf-8")

    # The wrapper execs this fake manager only after it has repaired 95.py.
    (app / "manager_82.py").write_text(
        "from pathlib import Path\n"
        "p = Path(__file__).with_name('95.py')\n"
        "print('MANAGER_SEES_SELF', p.is_file(), p.read_text(encoding='utf-8').strip())\n",
        encoding="utf-8",
    )
    env = dict(os.environ, JAFJ_APP_DIR=str(app), JAFJ_IMAGE_SELFBOT=str(clean))
    result = subprocess.run(["sh", str(START)], env=env, text=True,
                            capture_output=True, timeout=20)
    return result, target, clean


def scenario(kind):
    result, target, clean = run_bootstrap(kind)
    output = result.stdout + result.stderr
    good = (result.returncode == 0
            and "SELFBOOT: restored" in output
            and "MANAGER_SEES_SELF True # clean image selfbot" in output
            and target.is_file()
            and not target.is_symlink()
            and target.read_bytes() == clean.read_bytes())
    print(f"{kind}: {'PASS' if good else 'FAIL'}")
    if not good:
        print(output[-2000:])
    return good


def config_scenario():
    docker = (REPO / "Dockerfile").read_text(encoding="utf-8")
    railway = (REPO / "railway.json").read_text(encoding="utf-8")
    good = ("cp /app/95.py /opt/jafj/95.py" in docker
            and 'CMD ["sh", "/opt/jafj/railway-start.sh"]' in docker
            and '"startCommand": "sh /opt/jafj/railway-start.sh"' in railway)
    print(f"deployment config: {'PASS' if good else 'FAIL'}")
    return good


if __name__ == "__main__":
    ok = (scenario("broken-link")
          and scenario("stale-file")
          and config_scenario())
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
