#!/usr/bin/env python3
"""تشخیص «ریل‌وی قدیمی هنوز روشنه» — ضربان (beacon) بین نمونه‌های هم‌زمان.

دو سرویس/دیپلویِ هم‌زمان با یک توکن، آپدیت‌ها را از تلگرام نصفه‌ونیمه می‌گیرند:
گاهی دکمه به نسخه‌ی قدیمیِ بدون 95.py می‌رسد و «فایل 95.py پیدا نشد» می‌آید.
این تست منطق خالصِ beacon (parse/scan/هشدار) را بدون تلگرام بررسی می‌کند.
"""
import os, sys, time, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

spec = importlib.util.spec_from_file_location("m82", os.path.join(REPO, "manager_82.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def beacon(dep, inst, build, host, ts):
    return (f"{M.BEACON_TAG} <code>i={inst}</code> <code>d={dep}</code> "
            f"<code>b={build}</code> <code>h={host}</code> "
            f"<code>t={ts}</code>")


def scenario_own_beacon_recognized():
    print("--- 1: own beacon (same deploy id) recognized as mine ---")
    now = int(time.time())
    bt = beacon(M.DEPLOY_ID, M.INSTANCE_ID, M.BUILD_VERSION, M.HOST_LABEL, now)
    mine, foreign = M.scan_beacons([{"text": bt, "edit_ts": now}])
    ok = len(mine) == 1 and mine[0]["inst"] == M.INSTANCE_ID and foreign == []
    print("mine=%d foreign=%d" % (len(mine), len(foreign)))
    print("scenario1:", "PASS" if ok else "FAIL")
    assert ok


def scenario_foreign_beacon_detected():
    print("--- 2: a second live deployment is detected ---")
    now = int(time.time())
    mine_bt = beacon(M.DEPLOY_ID, M.INSTANCE_ID, M.BUILD_VERSION, M.HOST_LABEL, now)
    other_bt = beacon("deadbeef99", "aa11", "v0906-railway", "railway-old", now)
    mine, foreign = M.scan_beacons(
        [{"text": mine_bt, "edit_ts": now},
         {"text": other_bt, "edit_ts": now}])
    ok = len(mine) == 1 and len(foreign) == 1 and foreign[0]["dep"] == "deadbeef99"
    print("mine=%d foreign=%s" % (len(mine), [f["dep"] for f in foreign]))
    print("scenario2:", "PASS" if ok else "FAIL")
    assert ok


def scenario_stale_beacon_ignored():
    print("--- 3: an old/dead beacon (20 min, service gone) is not an alert ---")
    now = int(time.time())
    other_bt = beacon("deadbeef99", "aa11", "v0906-railway", "railway-old",
                      now - 1200)
    mine, foreign = M.scan_beacons([{"text": other_bt, "edit_ts": now - 1200}])
    ok = foreign == [] and mine == []
    print("foreign=%d (expected 0)" % len(foreign))
    print("scenario3:", "PASS" if ok else "FAIL")
    assert ok


def scenario_plain_messages_ignored():
    print("--- 4: normal chat text never parses as a beacon ---")
    now = int(time.time())
    for t in ("سلام", "🟢 ربات مدیر بالا آمد", "95.py پیدا نشد",
              "#JAFJBEACON بدون فیلد", ""):
        assert M.parse_beacon(t) is None, t
    mine, foreign = M.scan_beacons(
        [{"text": "یک پیام معمولی", "edit_ts": now},
         {"text": "به‌روزرسانی فایل سلف نشد", "edit_ts": now}])
    ok = not mine and not foreign
    print("scenario4:", "PASS" if ok else "FAIL")
    assert ok


def scenario_plain_format_beacon():
    print("--- 5: plain (non-html) beacon from another instance parses ---")
    now = int(time.time())
    plain = f"#JAFJBEACON i=ab12 d=fedcba98 b=v0906-railway h=railway-prod t={now}"
    p = M.parse_beacon(plain)
    mine, foreign = M.scan_beacons([{"text": plain, "edit_ts": now}])
    ok = bool(p) and p["dep"] == "fedcba98" and len(foreign) == 1
    print("parsed=%r foreign=%d" % (bool(p), len(foreign)))
    print("scenario5:", "PASS" if ok else "FAIL")
    assert ok


def scenario_deploy_id_stable_across_imports():
    print("--- 6: deploy id persists on the same data dir (file-backed) ---")
    d1 = M.DEPLOY_ID
    assert len(d1) >= 6 and all(c in "0123456789abcdef" for c in d1)
    # reload from scratch -> same BASE_DIR file -> same id
    spec2 = importlib.util.spec_from_file_location("m82b", os.path.join(REPO, "manager_82.py"))
    M2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(M2)
    ok = M2.DEPLOY_ID == d1
    print("first=%s second=%s same=%s" % (d1, M2.DEPLOY_ID, ok))
    print("scenario6:", "PASS" if ok else "FAIL")
    assert ok


def scenario_hint_on_hosted():
    print("--- 7: sync failure hint mentions the stale service on hosted ---")
    old_hosted = M.is_hosted()
    os.environ["RAILWAY_SERVICE_ID"] = "test"
    hint = M._self_sync_failure_hint("boom")
    os.environ.pop("RAILWAY_SERVICE_ID", None)
    ok = ("ریل‌وی" in hint or "Railway" in hint) and "سرویس" in hint
    print("hint:", hint.strip().splitlines()[0][:80])
    print("scenario7:", "PASS" if ok else "FAIL")
    assert ok


if __name__ == "__main__":
    scenario_own_beacon_recognized()
    scenario_foreign_beacon_detected()
    scenario_stale_beacon_ignored()
    scenario_plain_messages_ignored()
    scenario_plain_format_beacon()
    scenario_deploy_id_stable_across_imports()
    scenario_hint_on_hosted()
    print("ALL: PASS")
