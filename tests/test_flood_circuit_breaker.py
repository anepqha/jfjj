#!/usr/bin/env python3
"""مدار قطع‌کن فلود بزرگ (🧯 circuit breaker).

گزارش کاربر: «باید ۳۰ ثانیه باشد ولی ۱۵ ساعت ویت داده».

بررسی: هیچ مسیری در کد نمی‌تواند ۵۴۰۰۰ ثانیه بسازد — سقف خودی ساعتی
است (hour_cap_wait ≤ ۱ ساعت)، اضافه تطبیقی حداکثر ۱۲۰ ثانیه و آپ‌تایم
هم آرام رشد می‌کند. پس ۱۵ ساعت عددِ خودِ FloodWait تلگرام است: جریمه‌ی
«تعداد جوین روزانه» (مخصوصاً لینک خصوصی t.me/+…). ربات هم صادقانه تا
پایانش فریز شده بود.

مشکل واقعی: بعد از پایان جریمه، ربات با همان فاصلهٔ ۳۰ ثانیه برمی‌گشت
و جریمهٔ بعدی طولانی‌تر می‌شد (تشدید تصاعدی). فیکس:

  * فلود ≥ ۱۰ دقیقه → اضافهٔ فاصله مستقیم به سقف (۱۲۰ث) + «حالت احتیاط»:
    تا پایان جریمه فاصله برگشت نمی‌خورد (maybe_decay قفل می‌شود)
  * فلود جوین/لفت ≥ ۵ دقیقه → گیت چک هم تا ۳۰ دقیقه آرام می‌گیرد
    (حساب در جریمه است؛ چک اضافه بار اضافه است)
  * نوتیف صادقانه: عدد جریمه + شمار جوین امروز + راهنمای واقعی
    (فاصلهٔ ۹۰–۱۸۰ یا جوین کمتر در روز) — نه «صبر می‌کنم و ادامه می‌دهم»
  * کندشدن آپ‌تایم (۳۰ث بعد از ۳ ساعت + ۱۰ث هر ساعت) فیچر عمدی صاحب‌حساب
    است و دست‌نخورده می‌ماند.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_flood_circuit_breaker_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = REPO


DRIVER = r"""
import importlib.util, os, sys, time
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


# ── ۰) فیچر صاحب‌حساب دست‌نخورده: کندشدن آپ‌تایم ۳۰/۱۰ ──
eng = fresh_engine()
x = eng.ex_cfg()
check(x.get("adaptive_uptime_extra_sec") == 30
      and x.get("adaptive_uptime_per_hour_sec") == 10,
      "۰: کندشدن آپ‌تایم (۳۰ث + ۱۰ث/ساعت) حفظ شده — فیچر خود کاربر")
T = 1_700_000_000
eng.started = T - (5 * 3600)   # ۵ ساعت آپ‌تایم
check(eng.adaptive_extra(T) == (0, 50, 50),
      "۰: ۵ ساعت آپ‌تایم → +۵۰ ثانیه (۳۰ + ۲×۱۰) — همان فرمول خودش")
eng.started = T
print("DONE 0 uptime feature intact")


# ── ۱) فلود کوچک: مثل قبل +۱۵، بدون حالت احتیاط ──
eng = fresh_engine()
x = eng.ex_cfg()
check(eng.adaptive_on_flood(30, now=T) == 15, "۱: فلود ۳۰ث → +۱۵ مثل قبل")
check(int(x.get("_adaptive_hard_until", 0) or 0) == 0,
      "۱: فلود کوچک → حالت احتیاط فعال نمی‌شود")
print("DONE 1 small flood unchanged")


# ── ۲) فلود بزرگ: اضافه → سقف، حالت احتیاط روشن ──
eng = fresh_engine()
x = eng.ex_cfg()
ret = eng.adaptive_on_flood(54000, now=T)   # همان ۱۵ ساعتِ گزارش‌شده
check(ret == 120, "۲: فلود ۱۵ساعته → اضافه مستقیم روی سقف ۱۲۰ث")
check(int(x.get("_adaptive_hard_until")) == T + 6 * 3600,
      "۲: حالت احتیاط = پایان جریمه با سقف ۶ ساعت (min(w, 6h))")
emin, emax = eng.effective_join_gap(T)
check((emin, emax) == (150, 180), f"۲: فاصله موثر ۱۵۰–۱۸۰ث (پایه+۱۲۰) — got {emin}-{emax}")

eng_rt = fresh_engine()          # با زمان واقعی، چون وضعیت با now واقعی چک می‌شود
rt = int(time.time())
eng_rt.adaptive_on_flood(700, now=rt)
st_text = eng_rt.adaptive_status_text()
check("حالت احتیاط" in st_text, "۲: صفحه وضعیت حالت احتیاط را نشان می‌دهد")
print("DONE 2 big flood breaker")


# ── ۳) داخل حالت احتیاط، فاصله برگشت نمی‌خورد ──
eng.adaptive_maybe_decay(now=T + 3600)   # ۱ ساعت بعد — هنوز داخل حالت احتیاط
check(eng.ex_cfg().get("_adaptive_flood_extra") == 120,
      "۳: داخل حالت احتیاط → maybe_decay چیزی کم نمی‌کند")

# حالت احتیاط ۶ ساعته سقف دارد (min(w, 6h))
eng2 = fresh_engine()
eng2.adaptive_on_flood(54000 * 10, now=T)
check(int(eng2.ex_cfg().get("_adaptive_hard_until")) == T + 6 * 3600,
      "۳: جریمه خیلی بلند → حالت احتیاط حداکثر ۶ ساعت")

# بعد از پایان حالت احتیاط، کاهش تدریجی از سر می‌گیرد
eng3 = fresh_engine()
eng3.adaptive_on_flood(700, now=T)   # hard تا T+700
eng3.adaptive_maybe_decay(now=T + 4400)   # گذشته از hard؛ ۷۳ دقیقه از آخرین فلود
check(eng3.ex_cfg().get("_adaptive_flood_extra") == 105,
      "۳: بعد از حالت احتیاط → کاهش تدریجی (۱۲۰→۱۰۵) از سر می‌گیرد")
print("DONE 3 hold & resume")


# ── ۴) سیم‌کشی سورس ──
check('"_adaptive_hard_until": 0' in src, "۴: کلید hard_until در DEFAULTS")
check("if now < int(x.get(\"_adaptive_hard_until\", 0) or 0):" in src,
      "۴: maybe_decay داخل حالت احتیاط قفل است")
check("x[\"_adaptive_hard_until\"] = int(now + min(_w, 6 * 3600))" in src,
      "۴: فلود ≥۶۰۰ث → حالت احتیاط با سقف ۶ ساعت")
check("check_gate.penalize(min(w, 1800))" in src,
      "۴: فلود جوین/لفت ≥۳۰۰ث → گیت چک تا ۳۰ دقیقه آرام می‌گیرد")
check(src.count("check_gate.penalize(min(w, 1800))") == 2,
      "۴: هم در join_link و هم در leave_link")
check("سقف تعداد جوین روزانه" in src and "تبادل فاصله ۹۰ ۱۸۰" in src,
      "۴: نوتیف فلود بزرگ صادقانه است (علت + راهنما)")
check("this جریمه" not in src, "۴: متن نوتیف تمیز")
# نوتیف قدیمیِ بی‌توضیح فقط برای فلودهای کوچک بماند
check('await note(f"⏳ تبادل — {msg}' in src,
      "۴: فلود کوچک همان نوتیف کوتاه را دارد")
print("DONE 4 wiring")


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
    env["PORT"] = "8219"
    env["JAFJ_PORT"] = "8219"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- flood circuit breaker ---")
    reset()
    out, err, rc = run()
    if err.strip():
        print("--- driver stderr (tail) ---")
        print(err[-3000:])
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
