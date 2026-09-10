#!/usr/bin/env python3
"""آپدیت round2: تطبیقی هوشمند + چک دائمی + ضد اسپم چندگروهی.

Covers every behaviour introduced by ``round2.patch`` (already applied to
the code) that had NO test coverage before:

  1. Smart adaptive join gap (تطبیقی هوشمند)
     * defaults: adaptive_on, step 15 s, cap 120 s, decay 60 min,
       uptime threshold 3 h + 30 s (+10 s per extra hour);
     * ``adaptive_extra`` / ``effective_join_gap`` math (fresh, flood,
       uptime, boundary, off);
     * ``adaptive_on_flood`` escalates 15→30→…→cap and re-applies the
       join throttle;
     * ``adaptive_maybe_decay`` walks the extra back after quiet
       minutes and never below 0;
     * command surface ``تبادل تطبیقی`` (روشن/خاموش/ریست/flood/max/
       decay/uptime/extra + Persian digits + bad format + status).

  2. Permanent membership check (چک دائمی — فیکس لفت ۱۵ ثانیه‌ای)
     * defaults: permanent_check True, max hours 0 (تا ابد),
       max_strikes 1 (فوری), check window 10–20 s;
     * ``should_watch_joined``: forever / capped hours / off,
       including the exact 24 h boundary;
     * command surface ``تبادل دائمی`` (روشن/خاموش/ساعت N/ساعت ۰/وضعیت).

  3. Multi-group scan anti-spam (ضد اسپم چندگروهی)
     * defaults: scan_jitter 5–15 s, scan_last_time;
     * command ``تبادل اسکن تصادفی``: inactive for 1 group, active for
       2+, set/clamp/bad-format;
     * the scan loop itself only jitters when ≥2 groups and not manual
       (source-level pin, the loop lives inside connect_and_run).

  4. Synced reminder/check windows
     * ``تبادل فاصله یادآوری`` also re-times the permanent check and
       resets pending checks;
     * ``تبادل بررسی`` also re-times «نیومدی» reminders (both next_check
       and next_reminder get rescheduled).

  5. Migration of legacy settings
     * old defaults (check 15–30, interval 15/30, strikes 3, recheck 12,
       reminder 20–40, missing new keys) → round2 defaults;
     * deliberately custom values survive.

  6. Surfaces: status panel, dashboard, HELP, status texts, and the
     runtime wiring (FloodWait → adaptive, decay in join loop,
     should_watch_joined in the check loop).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_round2_update_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = REPO


DRIVER = r"""
import importlib.util, json, os, sys, time
sys.path.insert(0, os.getcwd())

spec = importlib.util.spec_from_file_location("m95", "95.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

src = open("95.py", encoding="utf-8").read()

FAILS = []


def check(cond, label):
    if cond:
        print("OK", label)
        return True
    print("FAIL", label)
    FAILS.append(label)
    return False


def fresh_engine():
    for f in ("jafj_settings.json", "jafj.db", "jafj_limits.json"):
        if os.path.exists(f):
            os.remove(f)
    return m.Engine()


# ════════════════════════════════════════════════════════════
# 1. Defaults introduced by round2
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()
must = {
    # تطبیقی هوشمند
    "adaptive_on": True,
    "adaptive_flood_step_sec": 15,
    "adaptive_flood_max_sec": 120,
    "adaptive_flood_decay_min": 60,
    "adaptive_uptime_threshold_hours": 3,
    "adaptive_uptime_extra_sec": 30,
    "adaptive_uptime_per_hour_sec": 10,
    "_adaptive_flood_extra": 0,
    "_adaptive_last_flood": 0,
    "_adaptive_last_decay": 0,
    # چک دائمی
    "permanent_check": True,
    "permanent_check_max_hours": 0,
    "max_strikes": 1,
    "recheck_hours": 0,
    "check_min_sec": 10,
    "check_max_sec": 20,
    # یادآوری همگام با چک
    "reminder_min_sec": 10,
    "reminder_max_sec": 20,
    # ضد اسپم چندگروهی
    "scan_jitter_min_sec": 5,
    "scan_jitter_max_sec": 15,
}
for k, v in must.items():
    check(x.get(k) == v, f"default {k}={x.get(k)!r} want {v!r}")
check(x.get("scan_last_time") == {}, "default scan_last_time empty")
print("DONE defaults")


# ════════════════════════════════════════════════════════════
# 2. adaptive_extra / effective_join_gap math
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()
T = 1_700_000_000
eng.started = T  # uptime 0 → below the 3 h threshold

check(eng.adaptive_extra(T) == (0, 0, 0), "fresh engine → no adaptive extra")
check(eng.effective_join_gap(T) == (30, 60), "fresh engine → base gap 30-60")
check((eng.join_thr.min_gap, eng.join_thr.max_gap) == (30, 60),
      "throttle starts at base gap")

# uptime slowdown: threshold boundary and per-hour growth
eng.started = T - 3 * 3600          # exactly at the threshold → not yet
check(eng.adaptive_extra(T) == (0, 0, 0), "uptime == 3h threshold → no extra")
eng.started = T - (3 * 3600 + 30)   # 1 s past the threshold → +30
check(eng.adaptive_extra(T) == (0, 30, 30), "uptime just past 3h → +30 s")
eng.started = T - (5 * 3600 + 30)   # 2 full extra hours → +30 +2*10
check(eng.adaptive_extra(T) == (0, 50, 50), "uptime 5h → +50 s (30+2*10)")
check(eng.effective_join_gap(T) == (80, 110), "effective gap = base + uptime")
eng.started = T                      # back to fresh

# flood escalation: +15 per FloodWait, throttle follows
check(eng.adaptive_on_flood(42, now=T) == 15, "1st flood → +15")
check(x.get("_adaptive_flood_extra") == 15, "flood extra stored (15)")
check(x.get("_adaptive_last_flood") == T and x.get("_adaptive_last_decay") == T,
      "flood timestamps stored")
check(eng.effective_join_gap(T) == (45, 75), "gap after 1 flood → 45-75")
check((eng.join_thr.min_gap, eng.join_thr.max_gap) == (45, 75),
      "throttle re-applied after flood")
eng.adaptive_on_flood(30, now=T + 5)
eng.adaptive_on_flood(10, now=T + 10)
check(x.get("_adaptive_flood_extra") == 45, "3 floods → +45")
check((eng.join_thr.min_gap, eng.join_thr.max_gap) == (75, 105),
      "throttle follows 3 floods")

# flood cap
x["adaptive_flood_max_sec"] = 40
eng.adaptive_on_flood(60, now=T + 15)
check(x.get("_adaptive_flood_extra") == 40, "flood extra capped at max (40)")
check((eng.join_thr.min_gap, eng.join_thr.max_gap) == (70, 100),
      "throttle respects cap (70-100)")

# adaptive off → everything back to base
x["adaptive_on"] = False
check(eng.adaptive_extra(T) == (0, 0, 0), "adaptive off → no extra")
check(eng.effective_join_gap(T) == (30, 60), "adaptive off → base gap")
check(eng.adaptive_on_flood(60, now=T) == 0, "adaptive off → flood ignored")
check(x.get("_adaptive_flood_extra") == 40, "adaptive off → extra untouched")
x["adaptive_on"] = True
print("DONE adaptive math")


# ════════════════════════════════════════════════════════════
# 3. adaptive_maybe_decay walks the extra back down
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()
T = 1_700_000_000
eng.started = T
eng.adaptive_on_flood(30, now=T)
eng.adaptive_on_flood(30, now=T + 1)          # extra = 30, last_decay = T+1

eng.adaptive_maybe_decay(now=T + 1 + 1800)    # only 30 quiet minutes
check(x.get("_adaptive_flood_extra") == 30, "decay: 30 quiet min < 60 → unchanged")

eng.adaptive_maybe_decay(now=T + 1 + 3601)    # 60 quiet minutes → −15
check(x.get("_adaptive_flood_extra") == 15, "decay: 60 quiet min → −15")

eng.adaptive_maybe_decay(now=T + 1 + 3601 + 7200)  # 2 more hours → −30 → floor 0
check(x.get("_adaptive_flood_extra") == 0, "decay: never below 0")

# no flood ever recorded → decay must not crash nor change anything
eng2 = fresh_engine()
eng2.adaptive_maybe_decay(now=T + 99999)
check(eng2.ex_cfg().get("_adaptive_flood_extra") == 0,
      "decay without any flood is a no-op")
print("DONE adaptive decay")


# ════════════════════════════════════════════════════════════
# 4. تبادل تطبیقی command surface
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()

out = eng.exchange_cmd("تطبیقی")
check("🧠 تطبیقیِ جوین" in out and "روشن" in out, "تطبیقی → status page on")

# خاموش → base gap everywhere, floods ignored
eng.adaptive_on_flood(30, now=int(time.time()))
out = eng.exchange_cmd("تطبیقی خاموش")
check("خاموش" in out, "تطبیقی خاموش → ack")
check(x.get("adaptive_on") is False, "تطبیقی خاموش → flag off")
check(eng.adaptive_extra() == (0, 0, 0), "تطبیقی خاموش → extra zeroed")
check((eng.join_thr.min_gap, eng.join_thr.max_gap) == (30, 60),
      "تطبیقی خاموش → throttle back to base")

out = eng.exchange_cmd("تطبیقی روشن")
check(x.get("adaptive_on") is True, "تطبیقی روشن → flag on")

# ریست zeroes the flood extras
eng.adaptive_on_flood(30, now=int(time.time()))
eng.adaptive_on_flood(30, now=int(time.time()))
out = eng.exchange_cmd("تطبیقی ریست")
check("صفر" in out, "تطبیقی ریست → ack")
check(x.get("_adaptive_flood_extra") == 0 and x.get("_adaptive_last_flood") == 0,
      "تطبیقی ریست → flood extra + last flood cleared")

# numeric knobs
out = eng.exchange_cmd("تطبیقی flood 20")
check(x.get("adaptive_flood_step_sec") == 20, "تطبیقی flood 20 → step 20")
out = eng.exchange_cmd("تطبیقی max 150")
check(x.get("adaptive_flood_max_sec") == 150, "تطبیقی max 150 → cap 150")
out = eng.exchange_cmd("تطبیقی decay 30")
check(x.get("adaptive_flood_decay_min") == 30, "تطبیقی decay 30 → 30 min")
out = eng.exchange_cmd("تطبیقی uptime 5")
check(x.get("adaptive_uptime_threshold_hours") == 5, "تطبیقی uptime 5 → 5 h")
out = eng.exchange_cmd("تطبیقی extra 40 12")
check(x.get("adaptive_uptime_extra_sec") == 40
      and x.get("adaptive_uptime_per_hour_sec") == 12,
      "تطبیقی extra 40 12 → 40 s + 12 s/h")

# Persian digits work too
out = eng.exchange_cmd("تطبیقی flood ۲۵")
check(x.get("adaptive_flood_step_sec") == 25, "تطبیقی flood ۲۵ → Persian digits")

# bad format → friendly error, value untouched
out = eng.exchange_cmd("تطبیقی flood الف")
check("فرمت" in out, "تطبیقی flood الف → فرمت error")
check(x.get("adaptive_flood_step_sec") == 25, "bad format leaves value intact")

# status text content
fret = eng.adaptive_on_flood(30, now=int(time.time()))
st_text = eng.adaptive_status_text()
check(f"اضافه از FloodWait: {fret}" in st_text, "status shows flood extra")
check("موثر فعلی" in st_text and "پایه" in st_text, "status shows base vs effective")
print("DONE adaptive commands")


# ════════════════════════════════════════════════════════════
# 5. چک دائمی: should_watch_joined + command surface
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()
now = int(time.time())

r1, _ = eng.db.ex_add(777001, "perm1", "@perm_test_1")
eng.db.ex_set(r1["id"], status="joined", joined_at=now - 10)
rec_fresh = eng.db.ex_get(r1["id"])
check(eng.should_watch_joined(rec_fresh, now) is True,
      "default: watch forever (تا ابد)")

out = eng.exchange_cmd("دائمی")
check("چک دائمی" in out and "تا ابد" in out, "دائمی → status page (تا ابد)")

# capped hours
out = eng.exchange_cmd("دائمی ساعت 24")
check("24" in out, "دائمی ساعت 24 → ack")
check(x.get("permanent_check_max_hours") == 24
      and x.get("recheck_hours") == 24, "دائمی ساعت 24 → stored 24/24")
r2, _ = eng.db.ex_add(777002, "perm2", "@perm_test_2")
eng.db.ex_set(r2["id"], status="joined", joined_at=now - 1 * 3600)
check(eng.should_watch_joined(eng.db.ex_get(r2["id"]), now) is True,
      "capped: 1 h old record still watched")
eng.db.ex_set(r2["id"], joined_at=now - 25 * 3600)
check(eng.should_watch_joined(eng.db.ex_get(r2["id"]), now) is False,
      "capped: 25 h old record dropped")
# exact boundary: 24 h exactly → still watched
eng.db.ex_set(r2["id"], joined_at=now - 24 * 3600)
check(eng.should_watch_joined(eng.db.ex_get(r2["id"]), now) is True,
      "capped: exactly 24 h → still watched")

# Persian zero → unlimited again
out = eng.exchange_cmd("دائمی ساعت ۰")
check("♾️" in out, "دائمی ساعت ۰ → ♾️ ack")
check(x.get("permanent_check_max_hours") == 0, "دائمی ساعت ۰ → stored 0")
eng.db.ex_set(r2["id"], joined_at=now - 999 * 3600)
check(eng.should_watch_joined(eng.db.ex_get(r2["id"]), now) is True,
      "unlimited again: very old record watched")

# خاموش → no watching at all (max hours is 0 here)
out = eng.exchange_cmd("دائمی خاموش")
check("خاموش" in out, "دائمی خاموش → ack")
check(x.get("permanent_check") is False, "دائمی خاموش → flag off")
eng.db.ex_set(r2["id"], joined_at=now - 10)
check(eng.should_watch_joined(eng.db.ex_get(r2["id"]), now) is False,
      "off + no cap → nothing watched")

# روشن → watching again
out = eng.exchange_cmd("دائمی روشن")
check(x.get("permanent_check") is True
      and x.get("permanent_check_max_hours") == 0
      and x.get("recheck_hours") == 0, "دائمی روشن → on + unlimited")
check(eng.should_watch_joined(eng.db.ex_get(r2["id"]), now) is True,
      "on again → watched")

status = eng.permanent_check_status_text()
check("چک دائمی عضویت" in status and "تا ابد" in status,
      "permanent status text renders")
print("DONE permanent check")


# ════════════════════════════════════════════════════════════
# 6. ضد اسپم چندگروهی: تبادل اسکن تصادفی
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()

x["groups"] = []                      # single/no group
out = eng.exchange_cmd("اسکن تصادفی")
check("غیرفعال" in out, "jitter display: inactive with <2 groups")

x["groups"] = ["@grp_a", "@grp_b"]    # 2 groups → active automatically
out = eng.exchange_cmd("اسکن تصادفی")
check("فعال" in out, "jitter display: active with 2 groups")

out = eng.exchange_cmd("اسکن تصادفی 8 25")
check("8" in out and "25" in out, "jitter set ack shows 8-25")
check(x.get("scan_jitter_min_sec") == 8
      and x.get("scan_jitter_max_sec") == 25, "jitter set → stored 8/25")

# clamping to sane bounds
eng.exchange_cmd("اسکن تصادفی 0 999")
check(x.get("scan_jitter_min_sec") == 1 and x.get("scan_jitter_max_sec") == 60,
      "jitter clamped to 1..60")

# bad format
out = eng.exchange_cmd("اسکن تصادفی الف")
check("فرمت" in out, "jitter bad format → فرمت error")

x["groups"] = []
print("DONE scan jitter commands")


# ════════════════════════════════════════════════════════════
# 7. Synced windows: فاصله یادآوری ↔ بررسی
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()
now = int(time.time())

# فاصله یادآوری also re-times the permanent check
rs, _ = eng.db.ex_add(555001, "sync1", "@sync_test_1")
eng.db.ex_set(rs["id"], status="joined", next_check=now + 999999)
out = eng.exchange_cmd("فاصله یادآوری 5 15")
check(x.get("reminder_min_sec") == 5 and x.get("reminder_max_sec") == 15,
      "فاصله یادآوری 5 15 → reminder window 5-15")
check(x.get("check_min_sec") == 5 and x.get("check_max_sec") == 15,
      "فاصله یادآوری syncs the check window to 5-15")
check(x.get("check_interval_sec") == 0, "mixed window → interval 0 (always random)")
check(eng.db.ex_get(rs["id"])["next_check"] == 0,
      "فاصله یادآوری resets pending checks")

# بررسی also re-times «نیومدی» reminders (check + reminder rescheduled)
rj, _ = eng.db.ex_add(555002, "sync2", "@sync_test_2")
eng.db.ex_set(rj["id"], status="joined", next_check=now + 999999)
rp, _ = eng.db.ex_add(555003, "sync3", "@sync_test_3")
eng.db.ex_set(rp["id"], status="pending", next_reminder=now + 999999)
out = eng.exchange_cmd("بررسی 12 25")
check(x.get("check_min_sec") == 12 and x.get("check_max_sec") == 25,
      "بررسی 12 25 → check window 12-25")
check(x.get("reminder_min_sec") == 12 and x.get("reminder_max_sec") == 25,
      "بررسی syncs the reminder window to 12-25")
check(eng.db.ex_get(rj["id"])["next_check"] == 0,
      "بررسی resets pending checks")
nr = eng.db.ex_get(rp["id"])["next_reminder"]
check(now - 2 <= nr <= now + 25,
      f"بررسی reschedules reminder into 12-25 s (got {nr - now})")
check("12" in out and "25" in out, "بررسی ack shows the new window")

# single-value form → fixed interval
out = eng.exchange_cmd("بررسی 15")
check(x.get("check_min_sec") == 15 and x.get("check_max_sec") == 15
      and x.get("check_interval_sec") == 15,
      "بررسی 15 → fixed 15 s (interval stored)")
print("DONE synced windows")


# ════════════════════════════════════════════════════════════
# 8. Migration of legacy settings → round2 defaults
# ════════════════════════════════════════════════════════════
def legacy_file(name, extra):
    old = json.loads(json.dumps(m.DEFAULTS))
    ex = old["exchange"]
    for k in ("adaptive_on", "adaptive_flood_step_sec", "adaptive_flood_max_sec",
              "adaptive_flood_decay_min", "adaptive_uptime_threshold_hours",
              "adaptive_uptime_extra_sec", "adaptive_uptime_per_hour_sec",
              "_adaptive_flood_extra", "_adaptive_last_flood",
              "_adaptive_last_decay", "scan_jitter_min_sec",
              "scan_jitter_max_sec", "scan_last_time",
              "permanent_check", "permanent_check_max_hours"):
        ex.pop(k, None)          # old installs don't have the new keys
    ex.update(extra)
    with open(name, "w", encoding="utf-8") as f:
        json.dump(old, f)
    return m.Settings(name)


st = legacy_file("legacy1.json", {
    "check_min_sec": 15, "check_max_sec": 30,   # old round1 default pair
    "check_interval_sec": 15,
    "max_strikes": 3, "recheck_hours": 12,       # old behaviour
    "reminder_min_sec": 20, "reminder_max_sec": 40,
})
e1 = st.data["exchange"]
check(e1["check_min_sec"] == 10 and e1["check_max_sec"] == 20,
      "migration: legacy check 15-30 → 10-20")
check(e1["check_interval_sec"] == 20, "migration: legacy interval 15 → 20")
check(e1["max_strikes"] == 1, "migration: legacy strikes 3 → 1 (فوری)")
check(e1["recheck_hours"] == 0, "migration: legacy recheck 12 h → 0 (تا ابد)")
check(e1["permanent_check"] is True, "migration: permanent_check default on")
check(e1["permanent_check_max_hours"] == 0, "migration: max hours default 0")
check(e1["reminder_min_sec"] == 10 and e1["reminder_max_sec"] == 20,
      "migration: legacy reminder 20-40 → 10-20")
check(e1["scan_jitter_min_sec"] == 5 and e1["scan_jitter_max_sec"] == 15,
      "migration: jitter keys filled with 5-15")
check(e1["scan_last_time"] == {}, "migration: scan_last_time filled")
check(e1["adaptive_on"] is True and e1["_adaptive_flood_extra"] == 0,
      "migration: adaptive keys filled with defaults")

# even older default pairs migrate too
st = legacy_file("legacy2.json", {"check_min_sec": 5, "check_max_sec": 15,
                                  "reminder_min_sec": 5, "reminder_max_sec": 5})
e2 = st.data["exchange"]
check(e2["check_min_sec"] == 10 and e2["check_max_sec"] == 20,
      "migration: ancient check pair 5-15 → 10-20")
check(e2["reminder_min_sec"] == 10 and e2["reminder_max_sec"] == 20,
      "migration: ancient reminder pair 5-5 → 10-20")

# deliberately custom values survive migration untouched
st = legacy_file("legacy3.json", {
    "check_min_sec": 11, "check_max_sec": 33, "check_interval_sec": 45,
    "max_strikes": 2, "recheck_hours": 5,
    "reminder_min_sec": 7, "reminder_max_sec": 9,
    "permanent_check": False,
})
e3 = st.data["exchange"]
check(e3["check_min_sec"] == 11 and e3["check_max_sec"] == 33,
      "migration keeps custom check window")
check(e3["check_interval_sec"] == 45, "migration keeps custom interval")
check(e3["max_strikes"] == 2, "migration keeps custom strikes")
check(e3["recheck_hours"] == 5, "migration keeps custom recheck hours")
check(e3["reminder_min_sec"] == 7 and e3["reminder_max_sec"] == 9,
      "migration keeps custom reminder window")
check(e3["permanent_check"] is False, "migration keeps explicit permanent off")
print("DONE migration")


# ════════════════════════════════════════════════════════════
# 9. Surfaces: panel, dashboard, HELP
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
x = eng.ex_cfg()
eng.adaptive_on_flood(30, now=int(time.time()))   # +15 adaptive

text = eng.exchange_text()
check("فاصله موثر (تطبیقی)" in text, "exchange panel shows effective gap")
check("چک عضویت دائمی" in text, "exchange panel shows permanent check line")
check("ضد اسپم چندگروهی" in text, "exchange panel shows anti-spam line")
check("فاصله Join پایه" in text, "exchange panel shows base gap")

x["enabled"] = True
panel = eng.panel()
check("🧠 تطبیقی: روشن" in panel, "dashboard shows adaptive state")
check("اضافه Flood 15" in panel, "dashboard shows flood extra")
x["enabled"] = False

check("تطبیقی هوشمند" in m.HELP and "تبادل تطبیقی flood 15" in m.HELP,
      "HELP documents the adaptive commands")
print("DONE surfaces")


# ════════════════════════════════════════════════════════════
# 10. Runtime wiring (source pins — the loop code lives inside
#     connect_and_run and cannot be imported directly)
# ════════════════════════════════════════════════════════════
check(src.count("eng.adaptive_on_flood(w)") >= 2,
      "FloodWait on join AND leave feeds the adaptive gap")
check("eng.adaptive_maybe_decay()" in src,
      "join loop decays the adaptive extra")
check("eng.effective_join_gap()" in src,
      "join loop uses the effective gap")
check("eng.should_watch_joined(rec)" in src,
      "periodic check honours the permanent watch window")
check("groups_count >= 2 and not manual" in src,
      "scan jitter only for >=2 groups in automatic scans")
check("random.randint(jitter_min, jitter_max)" in src,
      "scan jitter sleeps a random amount in range")
check('x.get("reminder_min_sec", x.get("check_min_sec", 10))' in src,
      "reminder delay falls back to the check window (synced)")
check('"permanent_check": True' in src or "'permanent_check': True" in src
      or '"permanent_check"' in src,
      "permanent_check key present in source")
check("permanent_check_max_hours" in src,
      "permanent_check_max_hours key present in source")
print("DONE wiring")


if FAILS:
    print("FAILED:", len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
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
    env["PORT"] = "8216"
    env["JAFJ_PORT"] = "8216"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- round2 update: adaptive + permanent check + scan jitter ---")
    reset()
    out, err, rc = run()
    if err.strip():
        print("--- driver stderr (tail) ---")
        print(err[-2000:])
    print("--- driver output ---")
    for line in out.splitlines():
        if line.startswith(("OK", "FAIL", "DONE", "FAILED", "  -")):
            print(line)
    if "DONE PASS" in out and "FAIL" not in out:
        print("ALL: PASS")
        return True
    print("ALL: FAIL")
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
