#!/usr/bin/env python3
"""دروازه‌ی عملکرد سراسری (OpGate) — بودجه‌ی فشار API تلگرام.

چرا: CheckGate فقط مسیر «بررسی عضویت» را پخش می‌کند. بقیه‌ی مسیرها — جوین،
لفت، ارسال پیام، اسکن گروه، رزولِ یوزرنیم — هرکدام برای خودشان کار می‌کردند
و فشارِ *کل* روی حساب هیچ‌جا اندازه‌گیری نمی‌شد؛ نتیجه‌اش FloodWait روی
مسیرهای نوشتنی بود (جوین/ارسال) درست وقتی که چک‌ها آرام گرفته بودند.

فیکس: OpGate سراسری روی Engine —
  * هر عملیات «واحدِ فشار» دارد (جوین ۳، لفت/چک/رزول ۲، ارسال/اسکن/نوت ۱)
  * پنجره‌ی غلتان ۶۰ ثانیه‌ای با بودجه (پیش‌فرض ۴۰ واحد/دقیقه)
  * حداقل فاصله‌ی سراسری بین دو عملیات → انفجار ساخته نمی‌شود
  * FloodWait → سقف سراسری تا پایان جریمه + کم‌شدن بودجه (با سقف)
  * ۳ فلود در ۱۰ دقیقه → حالت سکوت ۱۵ دقیقه‌ای
  * ۱۰ دقیقه بی‌فلودی → یک پله بودجه برمی‌گردد
  * پنل: «تبادل دروازه» + وضعیت/روشن/خاموش/بودجه/فاصله/ریست

این تست هم منطق کلاس را قطعی (با `now` دستی) بررسی می‌کند، هم سیم‌کشیِ
مسیرهای واقعی را (source pin + رفتارِ Engine/پنل/داشبورد/HELP).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_op_gate_run")
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
    for f in ("jafj_settings.json", "jafj.db", "jafj_limits.json", "jafj_ai.json"):
        if os.path.exists(f):
            os.remove(f)
    return m.Engine()


T = 1_700_000_000.0

# ════════════════════════════════════════════════════════════
# 1 — پیش‌فرض‌ها و وزنِ عملیات‌ها
# ════════════════════════════════════════════════════════════
og = m.DEFAULTS["opgate"]
check(og["on"] is True, "1: دروازه پیش‌فرض روشن است")
check(og["serialize"] is True, "1: صفِ تک‌نفره پیش‌فرض روشن است (دونه‌دونه)")
check(float(og["pause_min_sec"]) == 15 and float(og["pause_max_sec"]) == 20,
      "1: مکث پیش‌فرض بعد از هر عملیات ۱۵ تا ۲۰ ثانیه (درخواست صاحب‌حساب)")
check(int(og["budget_per_min"]) == 40, "1: بودجه پیش‌فرض ۴۰ واحد در دقیقه")
check(float(og["min_gap_sec"]) == 0.4, "1: حداقل فاصله پیش‌فرض ۰.۴ ثانیه")
check(int(og["window_sec"]) == 60, "1: پنجره پیش‌فرض ۶۰ ثانیه")
check(int(og["quiet_floods"]) == 3 and int(og["quiet_minutes"]) == 15,
      "1: ۳ فلود در ۱۰ دقیقه → ۱۵ دقیقه سکوت")
print("DONE 1 defaults")

g = m.OpGate(og)
check(g.cost("join") == 3.0, "1: جوین گران‌ترین عملیات است (۳ واحد)")
check(g.cost("leave") == 2.0 and g.cost("check") == 2.0
      and g.cost("resolve") == 2.0, "1: لفت/چک/رزول ۲ واحد")
check(g.cost("send") == 1.0 and g.cost("scan") == 1.0
      and g.cost("note") == 1.0, "1: ارسال/اسکن/نوت ۱ واحد")
check(g.cost("چیزناشناخته") == 1.0, "1: دسته‌ی ناشناخته = ۱ واحد (قفل نمی‌شود)")
check(g.budget() == 40.0, "1: بودجه‌ی مؤثر بدون جریمه = پایه")
print("DONE 1 costs")


# ════════════════════════════════════════════════════════════
# 2 — ریتم پایه: حداقل فاصله‌ی سراسری
# ════════════════════════════════════════════════════════════
g = m.OpGate(og)
check(g.wait("check", T) == 0.0, "2: دروازه‌ی تازه → صبر ندارد")
g.record("check", now=T)
# نکته: T یک timestamp واقعی است؛ دقت float در این بزرگی ~۱e-۷ است
check(abs(g.wait("check", T) - 0.4) < 1e-6, "2: بعد از یک عملیات → ۰.۴ ثانیه فاصله")
check(abs(g.wait("join", T + 0.2) - 0.2) < 1e-6, "2: فاصله باقی‌مانده درست حساب می‌شود")
check(g.wait("send", T + 0.5) == 0.0, "2: بعد از گذشتن فاصله → صفر")
check(g.wait("check", T) == g.wait("check", T), "2: wait بدون اثر جانبی است (چندبار صدا زده شود)")
check(g.load(T) == 2.0, "2: فشارِ پنجره فقط ۲ واحد (یک چک)")
print("DONE 2 min gap")


# ════════════════════════════════════════════════════════════
# 3 — بودجه‌ی پنجره‌ی غلتان
# ════════════════════════════════════════════════════════════
g = m.OpGate({"budget_per_min": 10, "min_gap_sec": 0.0, "window_sec": 60})
for i in range(5):
    g.record("send", now=T + i)          # ۵ × ۱ = ۵ واحد
check(g.load(T + 5) == 5.0, "3: ۵ ارسال = ۵ واحد فشار")
check(g.wait("send", T + 5) == 0.0, "3: نیمی از بودجه خالی است → صبر ندارد")
for i in range(5):
    g.record("send", now=T + 5 + i)      # حالا ۱۰ واحد = پر
check(g.load(T + 10) == 10.0, "3: بودجه پر شد (۱۰ از ۱۰)")
w = g.wait("send", T + 10)
check(w > 0, "3: پنجره‌ی پر → باید صبر کند")
check(abs(w - 50.0) < 1e-6, "3: صبر تا بیرون رفتن قدیمی‌ترین عملیات (۵۰ ثانیه)")
check(g.wait("join", T + 10) > 0, "3: عملیات گران‌تر هم پشت همان پنجره می‌ایستد")
# در T+61 عملیات T و T+1 بیرون رفته‌اند (۸ واحد از ۱۰ مانده)
check(g.load(T + 61) == 8.0, "3: پنجره غلتان است — فقط عملیات بیش از ۶۰ ثانیه حذف می‌شوند")
check(g.wait("send", T + 61) == 0.0, "3: با جای خالی در بودجه، ارسال رد می‌شود")
check(g.load(T + 70) == 0.0, "3: بعد از کل پنجره → فشار صفر می‌شود")
check(g.wait("send", T + 70) == 0.0, "3: و دروازه کاملاً آزاد است")
print("DONE 3 rolling budget")

# عملیاتِ گران‌تر از کل بودجه هرگز قفل دائمی نمی‌شود
g = m.OpGate({"budget_per_min": 2, "min_gap_sec": 0.0, "window_sec": 60})
check(g.budget() == 2.0, "3: بودجه‌ی کوچک پذیرفته می‌شود (کف ۱ واحد)")
for i in range(4):
    g.record("send", now=T + i)
check(g.wait("send", T + 4) > 0, "3: ارسالِ ارزان پشت پنجره‌ی پر می‌ایستد")
check(g.wait("join", T + 4) == 0.0,
      "3: جوین (۳) از بودجه (۲) گران‌تر → قفل دائمی نمی‌شود")
check(m.OpGate({"budget_per_min": 0.5}).budget() == 1.0,
      "3: کف بودجه ۱ واحد است (هیچ‌وقت قفل نمی‌شود)")
check(m.OpGate({"budget_per_min": 0}).base_budget == 40.0,
      "3: بودجه‌ی ۰ یعنی «تنظیم‌نشده» → همان پیش‌فرض ۴۰")
print("DONE 3 expensive op never deadlocks")


# ════════════════════════════════════════════════════════════
# 4 — FloodWait: سقف سراسری + جریمه‌ی بودجه
# ════════════════════════════════════════════════════════════
g = m.OpGate(og)
check(g.penalize(90, "join", now=T) is False,
      "4: یک فلود تنها → حالت سکوت نمی‌آید")
check(g.blocked(T), "4: بعد از فلود، دروازه بسته است")
check(abs(g.wait("send", T) - 92.0) < 1e-6, "4: صبر تا پایان جریمه (۹۰ + ۲)")
check(g.blocked(T + 92) is False, "4: بعد از پایان جریمه → باز")
check(abs(g.budget() - 34.0) < 1e-9, "4: یک فلود → ۶ واحد جریمه (۴۰ → ۳۴)")
for i in range(30):
    g.penalize(5, "check", now=T + 2000 + i)   # فلودهای پراکنده، بیرون بازه‌ی سکوت
check(abs(g.budget() - 16.0) < 1e-9, "4: جریمه سقف دارد (۴۰٪ پایه) — بی‌نهایت نمی‌شود")
check(g.stats(T + 2050)["penalty"] == 24.0, "4: آمار، جریمه‌ی سقف‌خورده را نشان می‌دهد")
print("DONE 4 flood penalty")


# ════════════════════════════════════════════════════════════
# 5 — حالت سکوت (چند فلود پشت‌سرهم)
# ════════════════════════════════════════════════════════════
g = m.OpGate(og)
check(g.penalize(30, "join", now=T) is False, "5: فلود ۱ → سکوت نه")
check(g.penalize(30, "send", now=T + 60) is False, "5: فلود ۲ → سکوت نه")
check(g.penalize(30, "check", now=T + 120) is True, "5: فلود ۳ در ۱۰ دقیقه → حالت سکوت")
st = g.stats(T + 120)
check(st["quiet"] is True and st["quiet_left"] == 900, "5: سکوت ۱۵ دقیقه‌ای شروع شد")
check(st["quiet_rounds"] == 1, "5: یک دور سکوت شمرده شد")
check(st["floods_recent"] == 3, "5: سه فلود در بازه‌ی اخیر")
check("حالت سکوت" in g.status_text(T + 120), "5: پنل حالت سکوت را نشان می‌دهد")
check(g.wait("join", T + 130) > 800, "5: در سکوت، wait عدد بزرگ می‌دهد تا مسیرها بی‌خیال شوند")
check(g.blocked(T + 1019) and not g.blocked(T + 1021),
      "5: سکوت دقیقاً بعد از ۹۰۰ ثانیه تمام می‌شود")
# فلودهای پراکنده (بیرون بازه‌ی ۱۰ دقیقه) سکوت نمی‌سازند
g = m.OpGate(og)
g.penalize(10, "join", now=T)
g.penalize(10, "join", now=T + 900)
check(g.penalize(10, "join", now=T + 1800) is False,
      "5: فلودهای با فاصله‌ی زیاد → سکوت نمی‌آید")
print("DONE 5 quiet mode")


# ════════════════════════════════════════════════════════════
# 6 — بازگشت تدریجی (decay) و ریست دستی
# ════════════════════════════════════════════════════════════
g = m.OpGate(og)
g.penalize(30, "join", now=T)
before = g.budget()
check(g.maybe_decay(T + 300) is False, "6: زودتر از ۱۰ دقیقه بی‌فلودی → چیزی برنمی‌گردد")
check(g.maybe_decay(T + 601) is True, "6: بعد از ۱۰ دقیقه بی‌فلودی → یک پله برمی‌گردد")
check(g.budget() > before, "6: بودجه بیشتر شد")
for i in range(10):
    g.maybe_decay(T + 1300 + i * 700)
check(g.budget() == 40.0, "6: جریمه تا صفر برمی‌گردد و از پایه رد نمی‌شود")
g.penalize(30, "join", now=T + 9000)
g.reset()
check(g.penalty == 0.0 and g.blocked(T + 9001) is False,
      "6: ریست → جریمه/سقف/سکوت صفر می‌شود")
check(g.load(T + 9001) >= 0, "6: ریست، فشارِ پنجره را دست نمی‌زند")
print("DONE 6 decay/reset")


# ════════════════════════════════════════════════════════════
# 7 — خاموش/روشن
# ════════════════════════════════════════════════════════════
g = m.OpGate(og)
g.record("send", now=T)
g.penalize(60, "join", now=T)
g.on = False
check(g.wait("join", T) == 0.0, "7: دروازه‌ی خاموش هیچ‌وقت صبر نمی‌دهد")
check("خاموش" in g.status_text(T), "7: پنل خاموش بودن را می‌گوید")
g.on = True
check(g.wait("join", T) > 0, "7: روشن که شود دوباره سقف فلود اعمال می‌شود")
print("DONE 7 on/off")


# ════════════════════════════════════════════════════════════
# 8 — نامتغیرِ اصلی: هیچ پنجره‌ای از بودجه رد نمی‌شود
# ════════════════════════════════════════════════════════════
g = m.OpGate({"budget_per_min": 20, "min_gap_sec": 0.05, "window_sec": 60})
t = T
ops = []
for i in range(80):                       # ۸۰ چک پشت‌سرهم، با احترام به wait
    w = g.wait("check", t)
    t += max(0.0, w) + 0.05
    g.record("check", now=t)
    ops.append(t)
worst = 0.0
for ts in ops:
    s = sum(2.0 for u in ops if ts - 60.0 < u <= ts)
    worst = max(worst, s)
check(worst <= 20.0 + 2.0,
      "8: در هیچ پنجره‌ی ۶۰ ثانیه‌ای بیش از بودجه (+یک عملیات) فشار نیست")
check(ops[-1] - ops[0] > 60.0, "8: بارِ زیاد عملاً پخش می‌شود (نه انفجار)")
print("DONE 8 rolling-window invariant")


# ════════════════════════════════════════════════════════════
# 9 — Engine و تنظیمات
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
check(isinstance(eng.op_gate, m.OpGate), "9: Engine یک دروازه‌ی عملکرد دارد")
check(eng.op_gate.base_budget == 40.0 and eng.op_gate.on is True,
      "9: دروازه از تنظیمات پیکربندی می‌شود")
check("opgate" in eng.st.data, "9: بخش opgate در تنظیمات هست")

# فایل تنظیمات قدیمی (بدون opgate) → پیش‌فرض‌ها پر می‌شوند
with open("legacy_opgate.json", "w", encoding="utf-8") as f:
    json.dump({"exchange": {"check_min_sec": 15, "check_max_sec": 30},
               "_cfg_migrated_v2": True}, f, ensure_ascii=False)
st2 = m.Settings("legacy_opgate.json")
check(st2.data["opgate"]["budget_per_min"] == 40
      and st2.data["opgate"]["on"] is True,
      "9: تنظیمات قدیمی، پیش‌فرض‌های دروازه را می‌گیرد (چیزی گم نمی‌شود)")

# مقدار سفارشی کاربر دست‌نخورده می‌ماند
with open("custom_opgate.json", "w", encoding="utf-8") as f:
    json.dump({"opgate": {"budget_per_min": 90, "on": False},
               "_cfg_migrated_v2": True}, f, ensure_ascii=False)
st3 = m.Settings("custom_opgate.json")
check(st3.data["opgate"]["budget_per_min"] == 90
      and st3.data["opgate"]["on"] is False,
      "9: تنظیم سفارشی کاربر بازنویسی نمی‌شود")
check(st3.data["opgate"]["quiet_minutes"] == 15,
      "9: کلیدهای گم‌شده از پیش‌فرض پر می‌شوند")
g3 = m.OpGate(st3.data["opgate"])
check(g3.base_budget == 90.0 and g3.on is False, "9: apply تنظیمات سفارشی درست است")
print("DONE 9 engine/settings")


# ════════════════════════════════════════════════════════════
# 10 — پنل: «تبادل دروازه …»
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
txt = eng.exchange_cmd("دروازه")
check("دروازه‌ی عملکرد" in txt and "روشن" in txt, "10: «تبادل دروازه» وضعیت می‌دهد")
check("واحد" in txt and "پنجره" in txt, "10: وضعیت، فشار و پنجره را نشان می‌دهد")
check(eng.cmd("تبادل", "عملکرد") == txt, "10: نام مستعار «تبادل عملکرد»")
check(eng.exchange_cmd("opgate") == txt, "10: نام مستعار لاتین opgate")

out = eng.exchange_cmd("دروازه خاموش")
check(eng.op_gate.on is False and "خاموش شد" in out, "10: «دروازه خاموش» دروازه را خاموش می‌کند")
check(eng.st["opgate"]["on"] is False, "10: خاموشی در تنظیمات ذخیره می‌شود")
check("خاموش" in eng.exchange_cmd("دروازه"), "10: وضعیت، خاموش را نشان می‌دهد")

out = eng.exchange_cmd("دروازه روشن")
check(eng.op_gate.on is True and eng.st["opgate"]["on"] is True,
      "10: «دروازه روشن» برمی‌گرداند و ذخیره می‌شود")

out = eng.exchange_cmd("دروازه بودجه ۶۰")
check(eng.op_gate.base_budget == 60.0 and "60" in out,
      "10: بودجه با عدد فارسیِ کاربر تنظیم می‌شود (۶۰ → ۶۰)")
check(eng.st["opgate"]["budget_per_min"] == 60, "10: بودجه در تنظیمات می‌ماند")
out = eng.exchange_cmd("دروازه بودجه 3")
check(eng.op_gate.base_budget == 8.0, "10: بودجه کف ۸ واحد دارد")
out = eng.exchange_cmd("دروازه بودجه 99999")
check(eng.op_gate.base_budget == 600.0, "10: بودجه سقف ۶۰۰ واحد دارد")
check("نامعتبر" in eng.exchange_cmd("دروازه بودجه الف"),
      "10: عدد نامعتبر پیام خطا می‌دهد")
eng.exchange_cmd("دروازه بودجه 40")

out = eng.exchange_cmd("دروازه فاصله 0.8")
check(abs(eng.op_gate.min_gap - 0.8) < 1e-9, "10: حداقل فاصله تنظیم می‌شود")
eng.exchange_cmd("دروازه فاصله 0.4")

# جریمه/سکوت با دستور ریست پاک می‌شود
eng.op_gate.penalize(120, "join")
eng.op_gate.penalize(120, "join")
eng.op_gate.penalize(120, "join")
check(eng.op_gate.blocked(), "10: سه فلود → سکوت فعال است")
out = eng.exchange_cmd("دروازه ریست")
check(eng.op_gate.blocked() is False and eng.op_gate.penalty == 0.0,
      "10: «دروازه ریست» سکوت و جریمه را پاک می‌کند")
eng.op_gate.penalize(120, "join")
eng.op_gate.penalize(120, "join")
eng.op_gate.penalize(120, "join")
out = eng.exchange_cmd("دروازه سکوت")
check(eng.op_gate.quiet_until == 0.0 and "لغو شد" in out,
      "10: «دروازه سکوت» فقط حالت سکوت را لغو می‌کند")
print("DONE 10 panel commands")


# ════════════════════════════════════════════════════════════
# 11 — سطوح گزارش: پنل تبادل، داشبورد، HELP، status
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
eng.op_gate.record("join")
eng.op_gate.record("check")
ex = eng.exchange_text()
check("دروازه‌ی عملکرد:" in ex and "دونه‌دونه" in ex,
      "11: پنل تبادل می‌گوید کارها دونه‌دونه انجام می‌شود")
check("مکث" in ex and "فشار" in ex and "`تبادل دروازه`" in ex,
      "11: پنل تبادل مکث، فشار و دستور را نشان می‌دهد")
eng.op_gate.on = False
check("دروازه‌ی عملکرد: خاموش" in eng.exchange_text(),
      "11: پنل تبادل خاموش بودن را نشان می‌دهد")
eng.op_gate.on = True

eng.write_status()
with open("jafj_status.json", encoding="utf-8") as f:
    sdata = json.load(f)
check("op_gate" in sdata, "11: فایل status کلید op_gate دارد")
check(sdata["op_gate"]["load"] == 5.0 and sdata["op_gate"]["budget"] == 40.0,
      "11: status فشار و بودجه‌ی زنده را می‌نویسد (۳ + ۲ = ۵)")
check(sdata["op_gate"]["serialize"] is True and sdata["op_gate"]["in_flight"] == 0,
      "11: status صفِ تک‌نفره و عملیاتِ در جریان را دارد")
check(sdata["op_gate"]["on"] is True and sdata["op_gate"]["quiet"] is False,
      "11: status وضعیت روشن/سکوت را دارد")

check("دروازه‌ی عملکرد (OpGate)" in m.HELP, "11: HELP بخش دروازه دارد")
check("تبادل دروازه مکث 1 3" in m.HELP and "تبادل دروازه پشت‌سرهم خاموش" in m.HELP,
      "11: HELP دستورهای مکث و صف را مستند می‌کند")
print("DONE 11 surfaces")


# ════════════════════════════════════════════════════════════
# 12 — سیم‌کشی مسیرهای واقعی (source pin)
# ════════════════════════════════════════════════════════════
k = src.find("async def peer_in_my_channel")
k2 = src.find("async def confirm_peer_membership", k)
seg = src[k:k2]
check('eng.op_gate.hold("check", max_wait=45)' in seg,
      "12: چک عضویت داخل صفِ تک‌نفره است")
check('eng.op_gate.penalize(w, "check")' in seg, "12: فلودِ چک دروازه را جریمه می‌کند")
check("if not allowed:" in seg and "return None" in seg,
      "12: چکِ پشتِ صفِ طولانی اصلاً زده نمی‌شود (نامشخص)")

k = src.find("async def join_link")
k2 = src.find("async def leave_link", k)
seg = src[k:k2]
check('async with eng.op_gate.hold("join"):' in seg,
      "12: جوین داخل صفِ تک‌نفره است")
check('eng.op_gate.penalize(w, "join")' in seg, "12: فلودِ جوین دروازه را جریمه می‌کند")

k = src.find("async def leave_link")
k2 = src.find("async def reply_joined", k)
seg = src[k:k2]
check('async with eng.op_gate.hold("leave"):' in seg
      and 'eng.op_gate.penalize(w, "leave")' in seg,
      "12: لفت هم در صف است و جریمه دارد")

k = src.find("async def deliver")
k2 = src.find("async def note", k)
seg = src[k:k2]
check('eng.op_gate.hold("send")' in seg and 'eng.op_gate.penalize(w, "send")' in seg,
      "12: صف ارسال هم دونه‌دونه می‌رود")

k = src.find("async def send_not_joined_reminder")
k2 = src.find("# ---------- پیدا کردن کانال طرف", k)
seg = src[k:k2]
check('eng.op_gate.hold("send", max_wait=180)' in seg,
      "12: پیام «نیومدی» هم در صف است")

k = src.find("async def scan_groups")
k2 = src.find("async def scan_loop", k)
seg = src[k:k2]
check('eng.op_gate.hold("scan")' in seg and 'eng.op_gate.penalize(w, "scan")' in seg,
      "12: اسکن گروه در صف است و جریمه دارد")

k = src.find("async def note(text)")
check('eng.op_gate.hold("note")' in src[k:k + 500],
      "12: گزارش‌های PV هم در همان صف می‌روند")

k = src.find("async def _say(")
check('eng.op_gate.hold("send", quick=True)' in src[k:k + 900],
      "12: پاسخِ پنل هم در صف است (بدون مکثِ بلند، تا کاربر معطل نماند)")

k = src.find("async def find_their_channel")
k2 = src.find("# ---------- دریافت درخواست تبادل", k)
check(src[k:k2].count('eng.op_gate.hold("resolve")') >= 2,
      "12: رزولِ کانال طرف (پیام‌ها + پروفایل) در صف است")

k = src.find("async def membership_loop")
k2 = src.find("# ── یادآوری عضو‌نشده", k)
check("eng.op_gate.maybe_decay()" in src[k:k2],
      "12: حلقه‌ی بررسی، جریمه‌ی دروازه را برمی‌گرداند")

check("self.op_gate = OpGate(self.st[\"opgate\"])" in src,
      "12: دروازه روی Engine ساخته می‌شود")
check("self.op_gate.apply(self.st[\"opgate\"])" in src,
      "12: reload، تنظیمات دروازه را تازه می‌کند")
check(src.count("eng.op_gate.hold(") >= 10,
      "12: همه‌ی مسیرهای API از hold رد می‌شوند (نه فقط چندتا)")
check("asyncio.Lock()" in src, "12: قفلِ واقعی asyncio ساخته می‌شود")
print("DONE 12 wiring")


# ════════════════════════════════════════════════════════════
# 13 — رفتارِ واقعی: چکِ پشتِ دروازه، «نامشخص» می‌دهد نه نتیجه‌ی غلط
# ════════════════════════════════════════════════════════════
k = src.find("async def peer_in_my_channel")
k2 = src.find("async def confirm_peer_membership", k)
fn = src[k:k2]
fn = "\n".join(ln[4:] if ln.startswith("    ") else ln for ln in fn.splitlines())


class FakeFloodWait(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class GateAsyncio:
    slept = []

    @staticmethod
    async def sleep(s):
        GateAsyncio.slept.append(s)


calls = []


class FakeClient:
    def __init__(self, mode):
        self.mode = mode

    async def __call__(self, req):
        calls.append(req)
        if self.mode == "flood":
            raise FakeFloodWait(90)
        return True


eng = fresh_engine()
eng.st.prof("standard")["channel"] = "@mychan"
eng.op_gate = m.OpGate({"budget_per_min": 4, "min_gap_sec": 0.0, "window_sec": 60})


class Req:
    pass


NOTES = []


async def fake_note(text):
    NOTES.append(text)


ns = {"eng": eng, "client": FakeClient("flood"), "asyncio": GateAsyncio,
      "GetParticipantRequest": lambda ch, uid: Req(),
      "UserNotParticipantError": type("UserNotParticipantError", (Exception,), {}),
      "FloodWaitError": FakeFloodWait,
      "check_gate": m.CheckGate(),
      "time": time, "secs": m.secs, "note": fake_note,
      "_warn_check_flood": {"last": 0}}
exec(fn, ns)
peer_in_my_channel = ns["peer_in_my_channel"]

import asyncio as _aio
res = _aio.run(peer_in_my_channel(12345))
check(res is None, "13: فلود → نتیجه «نامشخص» (نه عضو نیست)")
check(eng.op_gate.blocked(), "13: فلودِ چک، دروازه را بست")
check(eng.op_gate.last_flood_kind == "check", "13: دسته‌ی فلود ثبت شد")
check(any("FloodWait" in n for n in NOTES),
      "13: هشدار فلود به صاحب‌حساب هم می‌رود")

# حالا دروازه بسته است: چک بعدی اصلاً درخواست نمی‌زند
calls.clear()
res = _aio.run(peer_in_my_channel(12345))
check(res is None and len(calls) == 0,
      "13: وقتی دروازه بسته است هیچ GetParticipant زده نمی‌شود")
print("DONE 13 behaviour")


# ════════════════════════════════════════════════════════════
# 14 — هسته‌ی فیکس: کارها «دونه‌دونه» + مکث بینشان
#     (باگ اصلی: هفت حلقه‌ی موازی هم‌زمان درخواست API می‌زدند)
# ════════════════════════════════════════════════════════════
GATE_CFG = {"budget_per_min": 1000, "min_gap_sec": 0.0, "window_sec": 60,
            "pause_min_sec": 0.2, "pause_max_sec": 0.25, "serialize": True}


def burst(cfg, n=6, work=0.03):
    # n کارِ هم‌زمان را با هم هل می‌دهد و زمان‌بندیِ واقعی را برمی‌گرداند.
    g = m.OpGate(dict(cfg))
    st = {"in": 0, "peak": 0, "done": []}

    async def worker():
        async with g.hold("send") as ok:
            if not ok:
                return
            st["in"] += 1
            st["peak"] = max(st["peak"], st["in"])
            await _aio.sleep(work)          # شبیه یک درخواستِ واقعی API
            st["in"] -= 1
            st["done"].append(time.time())

    async def all_():
        await _aio.gather(*[worker() for _ in range(n)])

    t0 = time.time()
    _aio.run(all_())
    return g, st, time.time() - t0


# الف) با صفِ تک‌نفره: هیچ‌وقت دو کار روی هم نمی‌افتد
g, st, took = burst(GATE_CFG)
check(len(st["done"]) == 6, "14: هر شش کار انجام شدند (هیچ‌کدام گم نشد)")
check(st["peak"] == 1, "14: هیچ لحظه‌ای دو عملیات هم‌زمان در جریان نبود")
check(g.max_in_flight == 1, "14: دروازه هم بیشترین هم‌زمانی را ۱ ثبت کرد")
gaps = [b - a for a, b in zip(st["done"], st["done"][1:])]
check(len(gaps) == 5 and min(gaps) >= 0.19,
      "14: بین پایانِ هر کار و کار بعدی حداقل مکث افتاد")
check(max(gaps) < 1.0, "14: مکث بی‌دلیل هم بلند نشد")
check(took >= 6 * 0.03 + 5 * 0.19,
      "14: کلِ زمان = کار + مکث‌ها (یعنی واقعاً پشت‌سرهم رفتند)")
check(g.in_flight == 0 and g.queue_len == 0,
      "14: بعد از پایان، نه کاری در جریان است نه صفی مانده")
check(g.max_queue >= 2, "14: بقیه پشت قفل صف کشیده بودند (نه هم‌زمان)")
print("DONE 14 serialized")

# ب) همان بار، ولی با صفِ خاموش → هم‌پوشانی می‌شود (ثابت می‌کند باگ واقعی بود)
_off = dict(GATE_CFG)
_off["serialize"] = False
g2, st2, took2 = burst(_off)
check(st2["peak"] > 1,
      "14: با صفِ خاموش کارها هم‌زمان می‌شوند — همان باگی که گزارش شده بود")
check(took2 < took, "14: حالت موازی سریع‌تر است ولی روی هم می‌ریزد")
print("DONE 14 parallel baseline")

# ج) مکثِ تصادفی بعد از هر کار واقعاً قرعه می‌خورد
g3 = m.OpGate(GATE_CFG)
seen = set()
for _ in range(12):
    g3._after_op()
    seen.add(round(g3.next_gap, 4))
check(len(seen) > 3, "14: مکثِ بعدی تصادفی است (عدد ثابت نیست)")
check(min(seen) >= 0.2 - 1e-9 and max(seen) <= 0.25 + 1e-9,
      "14: مکث داخل بازه‌ی تنظیم‌شده می‌ماند")

# د) ورودِ تودرتو از همان تسک بن‌بست نمی‌کند (مثلاً note داخل یک چک)
g4 = m.OpGate(GATE_CFG)


async def nested():
    async with g4.hold("join") as ok1:
        async with g4.hold("note") as ok2:
            return bool(ok1 and ok2)


check(_aio.run(_aio.wait_for(nested(), 5)) is True,
      "14: ورودِ تودرتو از همان تسک بن‌بست نمی‌کند")

# ه) وقتی صف/سقف بلند است، کارِ غیرضروری بی‌خیال می‌شود (نه اینکه معطل بماند)
g5 = m.OpGate(GATE_CFG)
g5.penalize(600, "join")


async def skipper():
    async with g5.hold("check", max_wait=45) as ok:
        return ok


check(_aio.run(_aio.wait_for(skipper(), 5)) is False,
      "14: با سقفِ فلودِ بلند، چک بی‌درنگ False می‌گیرد")
print("DONE 14 gate behaviour")


# ════════════════════════════════════════════════════════════
# 15 — دستورهای تازه‌ی پنل (مکث / صفِ تک‌نفره)
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
out = eng.exchange_cmd("دروازه")
check("دونه‌دونه" in out and "مکث بعد از هر کار" in out,
      "15: وضعیت، صف و مکث را نشان می‌دهد")
out = eng.exchange_cmd("دروازه مکث 1 3")
check(abs(eng.op_gate.pause_min - 1.0) < 1e-9 and abs(eng.op_gate.pause_max - 3.0) < 1e-9,
      "15: «دروازه مکث 1 3» بازه‌ی مکث را تنظیم می‌کند")
check(eng.st["opgate"]["pause_min_sec"] == 1.0
      and eng.st["opgate"]["pause_max_sec"] == 3.0,
      "15: مکث در تنظیمات ذخیره می‌شود")
out = eng.exchange_cmd("دروازه مکث ۲")
check(abs(eng.op_gate.pause_min - 2.0) < 1e-9 and eng.op_gate.pause_max >= 2.0,
      "15: مکث با عدد فارسی و تک‌عدد هم کار می‌کند")
check("نامعتبر" in eng.exchange_cmd("دروازه مکث الف"),
      "15: مکثِ نامعتبر پیام خطا می‌دهد")

out = eng.exchange_cmd("دروازه پشت‌سرهم خاموش")
check(eng.op_gate.serialize is False and eng.st["opgate"]["serialize"] is False,
      "15: «پشت‌سرهم خاموش» صف را خاموش و ذخیره می‌کند")
check("موازی" in eng.exchange_cmd("دروازه"), "15: وضعیت، موازی بودن را هشدار می‌دهد")
out = eng.exchange_cmd("دروازه پشت‌سرهم روشن")
check(eng.op_gate.serialize is True and eng.st["opgate"]["serialize"] is True,
      "15: «پشت‌سرهم روشن» صف را برمی‌گرداند")
print("DONE 15 new commands")


# ════════════════════════════════════════════════════════════
# 16 — باگِ «سه تا نیومدی پشت‌سرهم»
# ════════════════════════════════════════════════════════════
# الف) پاسِ یادآوری در هر پاس حداکثر ۳ رکورد را پردازش می‌کند
k = src.find("# ── یادآوری عضو‌نشده")
k2 = src.find("# ۳) چک دوره‌ای", k)
seg = src[k:k2]
check("_rem_msgs >= 3" in seg,
      "16: پاسِ یادآوری سقفِ ۳ «پیام» در هر پاس دارد (رکورد نه — عضو شد/لفت راه خودش را دارد)")
check("await say_not_joined(rec)" in seg,
      "16: یادآوری از محافظِ ضدِ تکرار رد می‌شود")
check("elif sent is None:" in seg,
      "16: پیامِ ردشده «تلاش ناموفق» حساب نمی‌شود (لفتِ بی‌جا نمی‌دهد)")

# ب) محافظِ ضدِ تکرار: به یک نفر دو بار پشت‌سرهم «نیومدی» نمی‌رود
k = src.find("    def recently_messaged(rec):")
k2 = src.find("    async def send_not_joined_reminder(rec):", k)
fn = src[k:k2]
fn = "\n".join(ln[4:] if ln.startswith("    ") else ln for ln in fn.splitlines())

sent_log = []


async def fake_send(rec):
    sent_log.append(rec["id"])
    return True


eng = fresh_engine()
ns = {"eng": eng, "send_not_joined_reminder": fake_send}
exec(fn, ns)
say_not_joined = ns["say_not_joined"]

rec_a = {"id": 1, "peer_id": 777, "link": "@aaa"}
rec_b = {"id": 2, "peer_id": 888, "link": "@bbb"}

r1 = _aio.run(say_not_joined(rec_a))
check(r1 is True and sent_log == [1], "16: پیام اول می‌رود")
r2 = _aio.run(say_not_joined(rec_a))
check(r2 is None and sent_log == [1],
      "16: پیام دوم به همان نفر رد می‌شود (نه پشت‌سرهم)")
r3 = _aio.run(say_not_joined(rec_b))
check(r3 is True and sent_log == [1, 2],
      "16: نفرِ دوم پیام خودش را می‌گیرد (قفلِ سراسری نیست)")

# بعد از گذشتنِ بازه‌ی یادآوری، دوباره مجاز است
eng.op_gate.last_msg["777"] = time.time() - 600
r4 = _aio.run(say_not_joined(rec_a))
check(r4 is True and sent_log == [1, 2, 1],
      "16: بعد از گذشتن بازه، پیامِ بعدی مجاز است")

# د) مسیرِ ریپلایِ رویداد هم سابقه‌ی پیام را ثبت می‌کند (وگرنه حلقه پشتِ
#    سرش یک «نیومدی» دیگر می‌فرستد)
check('eng.op_gate.note_msg(str(getattr(sender, "id", 0)' in src,
      "16: پیامِ مسیرِ رویداد هم در سابقه ثبت می‌شود")

# ج) خودِ دروازه هم زمانِ پیام‌ها را نگه می‌دارد
gg = m.OpGate(m.DEFAULTS["opgate"])
check(gg.since_msg("x") is None, "16: قبل از هر پیام، سابقه‌ای نیست")
gg.note_msg("x", now=T)
check(abs(gg.since_msg("x", T + 5) - 5.0) < 1e-9, "16: سابقه‌ی پیام درست حساب می‌شود")
print("DONE 16 no burst")


# ════════════════════════════════════════════════════════════
# 17 — تایم‌اوتِ کاذبِ جوین (رگرسیونِ صف)
# ════════════════════════════════════════════════════════════
k = src.find('eng.log("info", "ex_join_try"')
seg = src[k:k + 1400]
check('eng.op_gate.wait("join")' in seg and "60 + min(900" in seg,
      "17: مهلتِ جوین = ۶۰ ثانیه + صفِ تخمینیِ دروازه")
check("timeout=60)" not in seg, "17: تایم‌اوتِ ثابتِ ۶۰ ثانیه حذف شد")
print("DONE 17 join timeout")


# ════════════════════════════════════════════════════════════
# 18 — رفتارِ واقعی با مکثِ پیش‌فرض ۱۵ ثانیه
# ════════════════════════════════════════════════════════════
g = m.OpGate(m.DEFAULTS["opgate"])
t = T
g.record("send", now=t)
g._after_op(now=t)
w = g.wait("send", now=t)
check(15.0 <= w <= 20.0,
      "18: با تنظیمِ پیش‌فرض، عملیات بعدی ۱۵ تا ۲۰ ثانیه بعد می‌رود")
g._after_op(now=t, quick=True)
check(g.wait("send", now=t) < 1.0,
      "18: جوابِ پنل (quick) پشتِ مکثِ ۱۵ ثانیه‌ای نمی‌ماند")
check('hold("send", quick=True)' in src,
      "18: پاسخِ پنل واقعاً quick صدا زده می‌شود")
print("DONE 18 default pacing")


print("FAILED " + "; ".join(FAILS) if FAILS else "DONE PASS")
if FAILS:
    sys.exit(1)
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
                       capture_output=True, text=True, timeout=180)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- op gate: global API pressure budget ---")
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
