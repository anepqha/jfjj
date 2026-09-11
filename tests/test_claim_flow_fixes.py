#!/usr/bin/env python3
"""فیکس جریان ادعای «جوین شدم»: پیام «نیومدی» + لفت تضمینی.

چهار باگ واقعی که کاربر گزارش کرد و این تست آن‌ها را قفل می‌کند:

  1. «نیومدی» (یا متن ثبت‌شده‌ی کاربر) به اونایی که نیومدن نمی‌رفت —
     چون ادعای «جوین شدم» بدون ریپلای به پیام ربات، دروازه‌ی گروه رد
     می‌شد (gate relaxation) و وقتی چک عضویت «نامشخص» می‌شد همه‌چیز
     بی‌صدا می‌چرخید (هشدار مالک + محدود شدن حلقه).

  2. از کانالشان لفت نمی‌داد — چون اگر ارسال یادآوری شکست می‌خورد
     (ریپلای خاموش/پیام مرجع پیدا نبود/خطای ارسال) حلقه تا ابد فقط
     دوباره زمان‌بندی می‌کرد و هیچ‌وقت به مرحله‌ی لفت نمی‌رسید.
     فیکس: بعد از ۳ تلاش ناموفق، مستقیم به لفت/لغو.

  3. ادعای «جوین شدم» چک نمی‌شد و بعداً کانال طرف جوین می‌شد —
     چون مسیر «عضویت نامشخص» طرف را بدون هیچ چکی approved می‌کرد.
     فیکس: نامشخص = بررسی دوباره‌ی زمان‌بندی‌شده؛ کانالِ تنظیم‌نشده =
     هیچ تأییدی نه، فقط هشدار.

  4. تازه‌کار پیش‌قدم که از اول نیامده بود، بی‌هشدار و بی‌درنگ لفت
     داده می‌شد (پیام بعد از لفت می‌رفت). فیکس: اول دور «نیومدی»،
     بعد لفت؛ لفت فوری فقط برای تقلب‌کننده (عضو شد، پیام گرفت، لفت داد).

حلقه‌ی یادآوری و حلقه‌ی چک دوره‌ای از سورس واقعی 95.py استخراج و با
دیتابیس واقعی و استاب‌های تلگرام اجرا می‌شوند (همان روش
test_exchange_notjoined_flow).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_claim_flow_fixes_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = REPO


DRIVER = r"""
import asyncio, importlib.util, os, sys, time
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


# ── استخراج حلقه‌ی یادآوری از سورس واقعی ──
a = src.find("# ── یادآوری عضو‌نشده: دو پیام «نیومدی»")
b = src.find("# ۳) چک دوره‌ای", a)
assert a > 0 and b > a, "reminder loop markers missing"
block = src[a:b]
lines = block.splitlines()
while lines and (lines[0].lstrip().startswith("#") or not lines[0].strip()):
    lines.pop(0)
while lines and not lines[-1].strip():
    lines.pop()
IND = 16
ded = "\n".join(ln[IND:] if ln.startswith(" " * IND) else ln for ln in lines)
harness_src = (
    "import time\n"
    "fa = m.fa\n"
    "async def _run(eng, x, stub):\n"
    "    now_rem = int(time.time())\n"
    "    confirm_peer_membership = stub.confirm\n"
    "    send_not_joined_reminder = stub.send_rem\n"
    "    leave_link = stub.leave\n"
    "    reminder_delay = stub.reminder_delay\n"
    "    membership_check_delay = stub.check_delay\n"
    "    note = stub.note\n"
    "    warn_membership_check_broken = stub.warn\n"
    "    check_gate = stub.gate\n"
    + "\n".join("    " + ln for ln in ded.splitlines())
    + "\n"
)
hns = {"m": m}
exec(harness_src, hns)
run_reminder = hns["_run"]


class Stub:
    def __init__(self, member_map=None, send_ok=True, gate=None):
        self.member_map = member_map or {}
        self.send_ok = send_ok
        self.sent = []
        self.left = []
        self.notes = []
        self.warns = []
        self.fast = []
        self.gate = gate or m.CheckGate(base_gap=0.0)

    async def confirm(self, pid, fast=False):
        self.fast.append(bool(fast))
        return self.member_map.get(pid)

    async def send_rem(self, rec):
        if not self.send_ok:
            return False
        self.sent.append(rec["id"])
        return True

    async def leave(self, link):
        self.left.append(link)
        return True, ""

    async def note(self, text):
        self.notes.append(text)

    async def warn(self, now):
        self.warns.append(int(now))

    @staticmethod
    def reminder_delay():
        return 15

    @staticmethod
    def check_delay():
        return 15

    @staticmethod
    def watch_delay(rec):
        # فاصله‌ی پلکانی چک نگهبانی در تست‌ها همان بازه‌ی پایه است.
        return 15


def mkRec(eng, peer, link, status, reminders=0, direction="in",
          replied=0, strikes=0, due_rem=True, due_check=False):
    r, _n = eng.db.ex_add(peer, f"p{peer}", link)
    now = int(time.time())
    eng.db.ex_set(r["id"], status=status, direction=direction,
                  reminders=reminders, replied=replied, strikes=strikes,
                  peer_id=peer, src_chat=111, src_msg=222,
                  next_reminder=now - 5 if due_rem else 0,
                  next_check=now - 5 if due_check else 0,
                  last_check=now - 5 if due_check else 0)
    return eng.db.ex_get(r["id"])


# ════════════════════════════════════════════════════════════
# F1 — ارسال یادآوری که همیشه شکست می‌خورد → بعد از ۳ تلاش، لفت
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
stub = Stub(member_map={601: False}, send_ok=False)   # ارسال همیشه fail
r = mkRec(eng, 601, "@fix601", "joined", direction="out")

asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(g["strikes"] == 1 and g["status"] == "joined",
      "F1: تلاش ۱ ناموفق → strikes=1، هنوز لفت نه")
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(g["strikes"] == 2 and g["status"] == "joined",
      "F1: تلاش ۲ ناموفق → strikes=2")
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(g["reminders"] == 2 and g["status"] == "joined",
      "F1: تلاش ۳ ناموفق → reminders=max_rem (آماده لفت)")
# نوبت لفت که ۵ ثانیه بعد است را رسیده کن و دوباره اجرا کن
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(r["link"] in stub.left and g["status"] == "left",
      "F1: بعد از ۳ شکست ارسال، لفت انجام شد (قبلاً تا ابد معطل می‌ماند)")
print("DONE F1 bounded send failures")


# ════════════════════════════════════════════════════════════
# F2 — چک عضویت همیشه «نامشخص» → بعد از ۵ بار، صبر بلند + هشدار مالک
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
stub = Stub(member_map={602: None})   # None = نامشخص
r = mkRec(eng, 602, "@fix602", "pending")

for i in range(4):
    eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
    asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(g["unk_streak"] == 4 and g["status"] == "pending",
      "F2: ۴ بار نامشخص → هنوز فاصله کوتاه (شمارنده‌ی جدا)")
check(g["strikes"] == 0,
      "F2: نتیجه‌ی نامشخص اخطارِ لفت (strikes) را آلوده نمی‌کند")
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(g["unk_streak"] >= 5, "F2: بار پنجم → شمارنده‌ی نامشخص گذشت")
check(g["next_reminder"] - int(time.time()) > 300,
      "F2: بعد از ۵ بار نامشخص → فاصله بلند (۱۰ دقیقه)")
check(len(stub.warns) == 1, "F2: هشدار مالک دقیقاً یک‌بار رفت")
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
check(len(stub.warns) == 2,
      "F2: پس از ۱۰ دقیقه‌ی نامشخصِ بیشتر، هشدار دوباره می‌رود (در استاب rate-limit ندارد)")
print("DONE F2 unknown bound + owner warning")


# ════════════════════════════════════════════════════════════
# F3 — چک دوره‌ای: تازه‌کارِ بی‌ادعا اول «نیومدی» می‌شنود، بعد لفت
# ════════════════════════════════════════════════════════════
a = src.find("# ۳) چک دوره‌ای")
b = src.find("# برای دقت فاصله‌ی یادآوری", a)
assert a > 0 and b > a, "periodic loop markers missing"
block = src[a:b]
lines = block.splitlines()
while lines and (lines[0].lstrip().startswith("#") or not lines[0].strip()):
    lines.pop(0)
while lines and not lines[-1].strip():
    lines.pop()
ded = "\n".join(ln[IND:] if ln.startswith(" " * IND) else ln for ln in lines)
ph = {}
pharness = (
    "import asyncio, time\n"
    "async def _run(eng, x, stub):\n"
    "    confirm_peer_membership = stub.confirm\n"
    "    send_not_joined_reminder = stub.send_rem\n"
    "    leave_link = stub.leave\n"
    "    reminder_delay = stub.reminder_delay\n"
    "    membership_check_delay = stub.check_delay\n"
    "    watch_delay_seconds = stub.watch_delay\n"
    "    warn_membership_check_broken = stub.warn\n"
    "    note = stub.note\n"
    "    check_gate = stub.gate\n"
    + "\n".join("    " + ln for ln in ded.splitlines())
    + "\n"
)
exec(pharness, ph)
run_periodic = ph["_run"]

eng = fresh_engine()
stub = Stub(member_map={603: False}, send_ok=True)
x = eng.ex_cfg()
x["enabled"] = True
r = mkRec(eng, 603, "@fix603", "joined", direction="out", replied=0,
          due_rem=False, due_check=True)

asyncio.run(run_periodic(eng, x, stub))
g = eng.db.ex_get(r["id"])
check(r["link"] not in stub.left and g["status"] == "joined",
      "F3: اولین نبودنِ بی‌ادعا → هنوز لفت نمی‌خورد")
check(g["reminders"] == 1 and len(stub.sent) == 1,
      "F3: اولین «نیومدی» رفت (قبلاً لفت بی‌هشدار بود)")
check(g["strikes"] == 1 and g["next_reminder"] > int(time.time()),
      "F3: دور یادآوری شروع شد (next_reminder آینده)")
check(g["next_check"] == 0, "F3: چک دوره‌ای تا پایان دور یادآوری دست نمی‌زند")

# دور یادآوری: دومین «نیومدی» → بعدش لفت
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, x, stub))
g = eng.db.ex_get(r["id"])
check(g["reminders"] == 2 and len(stub.sent) == 2, "F3: دومین «نیومدی» رفت")
eng.db.ex_set(r["id"], next_reminder=int(time.time()) - 5)
asyncio.run(run_reminder(eng, x, stub))
g = eng.db.ex_get(r["id"])
check(r["link"] in stub.left and g["status"] == "left",
      "F3: بعد از دو «نیومدی»، لفت انجام شد")
print("DONE F3 first-miss reminder cycle")


# ════════════════════════════════════════════════════════════
# F4 — تقلب‌کننده (عضو شد، پیام گرفت replied=1، لفت داد) → لفت بعد از ۲ نبودنِ تأییدشده
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
stub = Stub(member_map={604: False}, send_ok=True)
x = eng.ex_cfg()
x["enabled"] = True
x["max_strikes"] = 2
r = mkRec(eng, 604, "@fix604", "joined", direction="out", replied=1,
          due_rem=False, due_check=True)

# نبودنِ اول: هنوز لفت نمی‌دهیم (محافظت در برابر منفیِ کاذب)؛ فقط اخطار.
asyncio.run(run_periodic(eng, x, stub))
g = eng.db.ex_get(r["id"])
check(r["link"] not in stub.left and g["status"] == "joined" and g["strikes"] == 1,
      "F4: تقلب‌کننده (replied=1) → نبودنِ اول فقط اخطار است، لفت نه")
# نبودنِ دوم (تأییدشده) → حالا لفت می‌دهیم.
eng.db.ex_set(r["id"], next_check=int(time.time()) - 5)
asyncio.run(run_periodic(eng, x, stub))
g = eng.db.ex_get(r["id"])
check(r["link"] in stub.left and g["status"] == "left",
      "F4: تقلب‌کننده (replied=1) → بعد از ۲ نبودنِ تأییدشده لفت می‌دهیم")
check(len(stub.sent) == 1,
      "F4: فقط یک «نیومدی» (در مرز)؛ بعد از لفت تکرار نمی‌شود")
print("DONE F4 cheater leaves after 2 confirmed absences")


# ════════════════════════════════════════════════════════════
# F5 — مسیر «نامشخص» دیگر تأیید کورکورانه نمی‌کند (سورس‌پین + منطق)
# ════════════════════════════════════════════════════════════
check("ex_unknown_recheck" in src,
      "F5: چک نامشخص با کانالِ ست‌شده → بررسی دوباره‌ی زمان‌بندی‌شده")
check("ex_no_channel" in src,
      "F5: کانال تنظیم‌نشده → تأیید نمی‌شود (pending می‌ماند)")
check('status="approved", direction="in"' not in src,
      "F5: مسیر نامشخص دیگر مستقیم approved نمی‌کند")
check("warn_membership_check_broken" in src,
      "F5: هشدار مالک برای چکِ خراب تعریف شده")
check(src.find("async def warn_membership_check_broken")
      < src.find("async def membership_loop"),
      "F5: هشدار قبل از حلقه‌ها تعریف شده (در دسترس است)")

# منطق has_channel را با تنظیمات واقعی چک کن
eng = fresh_engine()
has_ch = any((eng.st.prof(t)["channel"] or "").strip()
             for t in ("standard", "vip"))
check(has_ch is False, "F5: بدون کانال → has_channel=False")
eng.st.prof("standard")["channel"] = "@my_chan"
has_ch = any((eng.st.prof(t)["channel"] or "").strip()
             for t in ("standard", "vip"))
check(has_ch is True, "F5: با کانال → has_channel=True")
print("DONE F5 no blind approve")


# ════════════════════════════════════════════════════════════
# F6 — دروازه‌ی گروه: ادعای بدون ریپلای از طرفِ دارای تبادل فعال رد نمی‌شود
# ════════════════════════════════════════════════════════════
check("gate_ok" in src and "gate_rec" in src,
      "F6: گیت گروه حالت استثنای تبادل فعال را دارد")
check('gate_rec.get("status") in ("pending", "approved",\n'
      '                                                          "joined")'
      in src.replace("local_claim and gate_rec\n", ""),
      "F6: استثنا فقط برای وضعیت‌های فعال (pending/approved/joined)")
# و منطقش را با دیتابیس واقعی چک کن
eng = fresh_engine()
rec, _ = eng.db.ex_add(605, "gate", "@fix605")
eng.db.ex_set(rec["id"], status="joined")
gr = eng.db.ex_by_peer(605)
gate_ok = (True and gr and gr.get("status") in ("pending", "approved", "joined"))
check(gate_ok is True, "F6: تبادل فعال → ادعای بدون ریپلای پردازش می‌شود")
gr = eng.db.ex_by_peer(606)   # این طرف هیچ رکوردی ندارد
gate_ok = bool(gr and gr.get("status") in ("pending", "approved", "joined"))
check(gate_ok is False, "F6: غریبه بدون تبادل → مثل قبل نادیده (ضد اسپم)")
eng.db.ex_set(rec["id"], status="left")
gr = eng.db.ex_by_peer(605)
gate_ok = bool(gr and gr.get("status") in ("pending", "approved", "joined"))
check(gate_ok is False, "F6: تبادل بسته‌شده (left) → نادیده")
print("DONE F6 gate relaxation")


# ════════════════════════════════════════════════════════════
# F7 — سقف فلود فعال → حلقه‌ها شمارنده نمی‌سوزانند و ۶۰ث صبر می‌کنند
# ════════════════════════════════════════════════════════════
eng = fresh_engine()
blocked_gate = m.CheckGate(base_gap=0.0)
blocked_gate.penalize(120, now=int(time.time()))          # سقف ۱۲۰ث فعال
stub = Stub(member_map={701: None}, gate=blocked_gate)    # چک نامشخص می‌دهد
r = mkRec(eng, 701, "@fix701", "pending")

asyncio.run(run_reminder(eng, eng.ex_cfg(), stub))
g = eng.db.ex_get(r["id"])
check(g["strikes"] == 0, "F7: داخل سقف فلود → شمارنده «نامشخص» نمی‌سوزد")
check(0 < g["next_reminder"] - int(time.time()) <= 65,
      "F7: داخل سقف فلود → ۶۰ ثانیه دیگر دوباره (نه چرخه کوتاه بی‌پایان)")
check(len(stub.warns) == 0, "F7: داخل سقف فلود → هشدار بی‌مورد نمی‌رود")

# و چک دوره‌ای هم همان‌طور
eng2 = fresh_engine()
stub2 = Stub(member_map={702: None}, gate=blocked_gate)
r2 = mkRec(eng2, 702, "@fix702", "joined", due_rem=False, due_check=True)
x2 = eng2.ex_cfg()
x2["enabled"] = True
asyncio.run(run_periodic(eng2, x2, stub2))
g2 = eng2.db.ex_get(r2["id"])
check(g2["strikes"] == 0, "F7: چک دوره‌ای داخل سقف فلود → شمارنده نمی‌سوزد")
check(0 < g2["next_check"] - int(time.time()) <= 65,
      "F7: چک دوره‌ای داخل سقف فلود → ۶۰ ثانیه صبر")
check(r2["link"] not in stub2.left,
      "F7: داخل سقف فلود لفت نصفه‌کاره نمی‌شود — بعد از باز شدن گیت انجام می‌شود")
print("DONE F7 flood-cooldown patience")


# ═══ F8 — سقف مادام‌العمر «نیومدی»: فقط ۲ بار برای هر تبادل، نه تا ابد ═══
import sqlite3 as _sq
old_db_path = "legacy_exchange.db"
if os.path.exists(old_db_path):
    os.remove(old_db_path)
_c = _sq.connect(old_db_path)
OLD_SCHEMA = (
    "CREATE TABLE exchange ( "
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "peer_id INTEGER, peer_name TEXT, link TEXT NOT NULL UNIQUE, "
    "channel_title TEXT, status TEXT NOT NULL DEFAULT 'pending', "
    "strikes INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, "
    "joined_at INTEGER, last_check INTEGER NOT NULL DEFAULT 0, note TEXT, "
    "src_chat INTEGER, src_msg INTEGER, "
    "replied INTEGER NOT NULL DEFAULT 0, direction TEXT NOT NULL DEFAULT 'in', "
    "reminders INTEGER NOT NULL DEFAULT 0, "
    "next_reminder INTEGER NOT NULL DEFAULT 0, "
    "next_check INTEGER NOT NULL DEFAULT 0)")
_c.execute(OLD_SCHEMA)
_c.execute("INSERT INTO exchange (peer_id, link, created_at) VALUES (1, '@old', 0)")
_c.commit()
_c.close()
eng_mig = m.DB(old_db_path)
cols = {r[1] for r in eng_mig.conn.execute("PRAGMA table_info(exchange)")}
check("reminders_total" in cols,
      "F8: \u0633\u062a\u0648\u0646 reminders_total \u0631\u0648\u06cc \u062f\u06cc\u062a\u0627\u0628\u06cc\u0633 \u0642\u062f\u06cc\u0645\u06cc \u0645\u0647\u0627\u062c\u0631\u062a \u0634\u062f")
row = eng_mig._x("SELECT * FROM exchange WHERE link='@old'", (), "one")
check(row["reminders_total"] == 0, "F8: \u0631\u06a9\u0648\u0631\u062f \u0642\u062f\u06cc\u0645\u06cc \u0628\u0627 \u0645\u0642\u062f\u0627\u0631 \u0670")

check("ex_reminder_cap" in src,
      "F8: \u0633\u0642\u0641 \u0645\u0637\u0644\u0642 \u062f\u0627\u062e\u0644 send_not_joined_reminder (\u062a\u0646\u0647\u0627 \u0646\u0642\u0637\u0647 \u0627\u0631\u0633\u0627\u0644)")
check('if int(rec.get("reminders_total") or 0) >= _max_rem:' in src,
      "F8: \u0634\u0631\u0637 \u0633\u0642\u0641 \u0645\u0648\u062c\u0648\u062f \u0627\u0633\u062a")
check(src.count("reminders_total=0") >= 3,
      "F8: \u0631\u06cc\u0633\u062a \u0641\u0642\u0637 \u062f\u0631 \u0646\u0642\u0627\u0637 \u0639\u0636\u0648\u06cc\u062a \u0648\u0627\u0642\u0639\u06cc (\u06f3 \u0646\u0642\u0637\u0647)")
check("can_more and not active_window" in src,
      "F8: \u0627\u0631\u0633\u0627\u0644 \u0641\u0648\u0631\u06cc \u0627\u062f\u0639\u0627 \u0647\u0645 \u062a\u0627\u0628\u0639 \u0633\u0642\u0641 \u0645\u0627\u062f\u0627\u0645\u200c\u0627\u0644\u0639\u0645\u0631 \u0627\u0633\u062a")
check("reminders_total=total_sent + (1 if sent_now else 0)" in src,
      "F8: \u0645\u0633\u06cc\u0631 \u0627\u062f\u0639\u0627 \u0634\u0645\u0627\u0631\u0646\u062f\u0647 \u0631\u0627 \u0628\u0627\u0644\u0627 \u0645\u06cc\u200c\u0628\u0631\u062f")

# تابع واقعی ارسال «نیومدی» را از سورس استخراج کن (سقف مادام‌العمر داخلش است)
a_r = src.find("async def send_not_joined_reminder")
b_r = src.find("# ---------- پیدا کردن کانال طرف", a_r)
assert a_r > 0 and b_r > a_r, "send_not_joined_reminder markers missing"
blk = src[a_r:b_r]
blines = blk.splitlines()
blines = blines[1:]   # فقط خط امضا حذف شود؛ بدنه (حتی داک‌استرینگ) بماند
while blines and not blines[-1].strip():
    blines.pop()
ded_r = "\n".join(ln[8:] if ln.startswith(" " * 8) else ln for ln in blines)


class FakeClientCap:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, body, reply_to=None, **kw):
        self.sent.append((chat, body, reply_to))
        return object()


def make_real_sender(eng, client):
    ns = {"eng": eng, "client": client, "DRY_RUN": False,
          "asyncio": asyncio, "DEFAULT_MSG_NO": m.DEFAULT_MSG_NO}
    exec("async def send_not_joined_reminder(rec):\n"
         + "\n".join("    " + ln for ln in ded_r.splitlines()) + "\n", ns)
    return ns["send_not_joined_reminder"]


# رفتاری با تابع واقعی:
eng_real = fresh_engine()
eng_real.st.prof("standard")["channel"] = "@my_real_chan"
client_cap = FakeClientCap()
real_send = make_real_sender(eng_real, client_cap)
r_cap, _ = eng_real.db.ex_add(810, "capreal", "@cap810")
eng_real.db.ex_set(r_cap["id"], src_chat=111, src_msg=222, reminders_total=2)
got = asyncio.run(real_send(eng_real.db.ex_get(r_cap["id"])))
check(got is False and len(client_cap.sent) == 0,
      "F8-رفتاری: total=۲ → تابع واقعی ارسال نمی‌کند")
eng_real.db.ex_set(r_cap["id"], reminders_total=1)
got = asyncio.run(real_send(eng_real.db.ex_get(r_cap["id"])))
check(got is True and len(client_cap.sent) == 1,
      "F8-رفتاری: total=۱ → ارسال می‌شود (بار دوم مجاز)")

# دو دور متوالی بدون عضویت → چرخهٔ تازه هیچ پیام تازه‌ای نمی‌سازد
eng_tot = fresh_engine()
real_send_tot = make_real_sender(eng_tot, FakeClientCap())
rt, _ = eng_tot.db.ex_add(801, "cap", "@cap801")
eng_tot.db.ex_set(rt["id"], reminders=2, reminders_total=2,
                  status="left", note="\u0646\u06cc\u0648\u0645\u062f \u2190 \u0644\u0641\u062a \u062f\u0627\u062f\u0645")
eng_tot.db.ex_set(rt["id"], status="pending", reminders=0,
                  next_reminder=int(time.time()) - 5)
stub_cap = Stub(member_map={801: False})
stub_cap.send_rem = real_send_tot        # تابع واقعی با سقف مادام‌العمر
asyncio.run(run_reminder(eng_tot, eng_tot.ex_cfg(), stub_cap))
g_cap = eng_tot.db.ex_get(rt["id"])
check(len(stub_cap.sent) == 0,
      "F8: \u0686\u0631\u062e\u0647 \u062a\u0627\u0632\u0647 \u0628\u062f\u0648\u0646 \u0639\u0636\u0648\u06cc\u062a \u2190 \u0647\u06cc\u0686 \u00ab\u0646\u06cc\u0648\u0645\u062f\u06cc\u00bb \u062a\u0627\u0632\u0647 \u0646\u0645\u06cc\u200c\u0631\u0648\u062f")
check(int(g_cap["strikes"] or 0) >= 1,
      "F8: \u062a\u0644\u0627\u0634\u200c\u0647\u0627\u06cc \u0633\u0627\u06a9\u062a \u0634\u0645\u0631\u062f\u0647 \u0645\u06cc\u200c\u0634\u0648\u062f")
for _ in range(3):
    eng_tot.db.ex_set(rt["id"], next_reminder=int(time.time()) - 5)
    asyncio.run(run_reminder(eng_tot, eng_tot.ex_cfg(), stub_cap))
g_cap = eng_tot.db.ex_get(rt["id"])
check(g_cap["status"] == "failed" and len(stub_cap.sent) == 0,
      "F8: \u0628\u0639\u062f \u0627\u0632 \u06f3 \u062a\u0644\u0627\u0634 \u0633\u0627\u06a9\u062a \u2190 \u062a\u0628\u0627\u062f\u0644 \u0628\u062f\u0648\u0646 \u067e\u06cc\u0627\u0645 \u0628\u0633\u062a\u0647 \u0634\u062f")

check(int(g_cap["reminders_total"] or 0) == 2,
      "F8: شمارندهٔ مادام‌العمر روی ۲ ماند — پیام سوم ساخته نشد")

# عضویت واقعی شمارنده را صفر می‌کند
eng_tot.db.ex_set(rt["id"], status="joined", reminders_total=2)
eng_tot.db.ex_set(rt["id"], next_reminder=int(time.time()) - 5)
stub_mem = Stub(member_map={801: True})
asyncio.run(run_reminder(eng_tot, eng_tot.ex_cfg(), stub_mem))
g_mem = eng_tot.db.ex_get(rt["id"])
check(g_mem["reminders_total"] == 0,
      "F8: \u0639\u0636\u0648 \u0634\u062f \u2190 \u0634\u0645\u0627\u0631\u0646\u062f\u0647 \u0635\u0641\u0631 \u0634\u062f (\u062f\u0648\u0631 \u0628\u0639\u062f\u06cc \u062d\u0642 \u067e\u06cc\u0627\u0645 \u062f\u0627\u0631\u062f)")
print("DONE F8 lifetime cap")


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
    env["PORT"] = "8217"
    env["JAFJ_PORT"] = "8217"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- claim-flow fixes: «نیومدی» + لفت تضمینی ---")
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
