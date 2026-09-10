#!/usr/bin/env python3
"""ضد-فلودِ بررسی عضویت: CheckGate سراسری.

باگ: چک دائمی هر رکورد را هر ۱۰–۲۰ ثانیه چک می‌کند (تا ابد) و هر چک تا
۲ بار GetParticipantRequest می‌زند؛ سه مسیر همزمان (حلقه‌ی یادآوری،
چک دوره‌ای، پیام ورودی) بدون هماهنگی می‌زدند. با ۲۰ رکورد جوین‌شده
یعنی ۶۰–۱۲۰ درخواست عضویت در دقیقه → FloodWait «الکی». ضمناً فلودِ
چک، تروتیل ارسال را هم جریمه می‌کرد و کل ربات فریز می‌شد.

فیکس: CheckGate سراسری —
  * حداقل فاصله بین درخواست‌ها؛ با تعداد رکوردها خودکار بلند می‌شود
  * روی FloodWait، سقف سراسری: همه‌ی مسیرها تا پایان آن درخواست نمی‌زنند
  * بعد از ۵ دقیقه بدون Flood، فاصله کم‌کم برمی‌گردد
  * فلود بلند (>45s) → درخواست اصلاً زده نمی‌شود (None بی‌درنگ)
  * حلقه‌ی membership هر دور set_load + maybe_decay را صدا می‌زند
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_check_gate_flood_run")
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


# ════════════════════════════════════════════════════════════
# G1 — منطق پایه CheckGate
# ════════════════════════════════════════════════════════════
T = 1_700_000_000.0
g = m.CheckGate(base_gap=1.5)
check(g.wait(T) == 0.0, "G1: گیت تازه → منتظر نمی‌ماند")
g.record(T)
check(g.wait(T) == 1.5, "G1: بعد از یک درخواست → ۱.۵ ثانیه فاصله")
check(0.49 <= g.wait(T + 1.0) <= 0.51, "G1: یک ثانیه بعد → ۰.۵ ثانیه مانده")
check(g.wait(T + 2.0) == 0.0, "G1: بعد از گذشت فاصله → صفر")
print("DONE G1 base pacing")


# ════════════════════════════════════════════════════════════
# G2 — penalize: سقف سراسری + فاصله‌ی بلندتر
# ════════════════════════════════════════════════════════════
g = m.CheckGate(base_gap=1.5)
g.penalize(90, now=T)   # FloodWait ۹۰ ثانیه‌ای
check(g.blocked(T), "G2: بعد از فلود → گیت بسته است")
check(g.wait(T) > 89, "G2: باید تا پایان فلود (۹۰+) صبر کند")
check(not g.blocked(T + 92), "G2: بعد از پایان فلود → گیت باز")
check(g.flood_extra == 1.5, "G2: فاصله‌ی اضافه +۱.۵ شد")
# چند فلود پشت‌سرهم → سقف ۲۰ ثانیه اضافه
for i in range(20):
    g.penalize(5, now=T + 100 + i)
check(g.flood_extra == 20.0, "G2: اضافه سقف‌خورده روی ۲۰ ثانیه (نامحدود نمی‌شود)")
check(g.gap() == 21.5, "G2: گاپ موثر = پایه + اضافه")
print("DONE G2 flood penalty")


# ════════════════════════════════════════════════════════════
# G3 — set_load: با رکورد بیشتر، فاصله بلندتر (پخش عادلانه)
# ════════════════════════════════════════════════════════════
g = m.CheckGate(base_gap=1.5)
g.set_load(0)
check(g.base_gap == 1.5, "G3: بدون رکورد → حداقل ۱.۵ ثانیه")
g.set_load(10)
check(g.base_gap == 6.0, "G3: ۱۰ رکورد → ۶ ثانیه (10×0.6)")
g.set_load(100)
check(g.base_gap == 8.0, "G3: ۱۰۰ رکورد → سقف ۸ ثانیه")
# نتیجه: نرخ درخواست همیشه ≤ ~۷.۵ در دقیقه؛ «تعداد رکورد» دیگر فلود نمی‌سازد
print("DONE G3 load spreading")


# ════════════════════════════════════════════════════════════
# G4 — maybe_decay: بعد از ۵ دقیقه بدون فلود، کم‌کم برگرد
# ════════════════════════════════════════════════════════════
g = m.CheckGate(base_gap=1.5)
g.penalize(10, now=T)
g.penalize(10, now=T + 1)          # extra=3.0
g.maybe_decay(now=T + 60)          # فقط ۱ دقیقه → هیچ
check(g.flood_extra == 3.0, "G4: ۱ دقیقه بدون فلود → هنوز کاهش نه")
for i in range(5):                  # هر ۵ دقیقه یک ثانیه کم کن
    g.maybe_decay(now=T + 1 + 300 * (i + 1))
check(abs(g.flood_extra - 0.0) < 0.001, "G4: بعد از ۲۵ دقیقه بی‌فلود → صفر")
g.maybe_decay(now=T + 999999)      # صفر است → crash نه، تغییر نه
check(g.flood_extra == 0.0, "G4: decay روی صفر بی‌خطر است")
print("DONE G4 decay")


# ════════════════════════════════════════════════════════════
# G5 — peer_in_my_channel واقعی از گیت رد می‌شود (رفتاری با سورس واقعی)
# ════════════════════════════════════════════════════════════
a = src.find("async def peer_in_my_channel")
b = src.find("async def confirm_peer_membership", a)
assert a > 0 and b > a, "peer_in_my_channel markers missing"
block = src[a:b]
lines = block.splitlines()
# فقط خط امضا و کامنت جداکننده را بردار؛ بدنه با همین نام پارامترها بسته می‌شود
lines = [ln for ln in lines
         if not ln.lstrip().startswith("async def peer_in_my_channel")]
ded = "\n".join(ln[8:] if ln.startswith(" " * 8) else ln for ln in lines)

harness = (
    "import asyncio, time\n"
    "async def run_pimc(eng, client, gate, warnflood, user_id):\n"
    "    GetParticipantRequest = stub_gpr\n"
    "    UserNotParticipantError = stub_unp\n"
    "    FloodWaitError = stub_fwe\n"
    "    check_gate = gate\n"
    "    _warn_check_flood = warnflood\n"
    "    secs = m.secs\n"
    "    async def note(text):\n"
    "        notes.append(text)\n"
    + "\n".join("    " + ln for ln in ded.splitlines())
    + "\n"
)

# استاب‌های تلگرام
class stub_unp(Exception):
    pass


class stub_fwe(Exception):
    def __init__(self, seconds=30):
        self.seconds = seconds


class stub_gpr:
    def __init__(self, ch, uid):
        self.ch, self.uid = ch, uid


class FakeClient:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    async def __call__(self, req, **kw):
        self.calls.append(time.time())
        if self.mode == "member":
            return object()
        if self.mode == "notmember":
            raise stub_unp()
        if self.mode == "flood":
            raise stub_fwe(90)
        raise RuntimeError("boom")


class FakeThr:
    def __init__(self):
        self.penalties = []

    def penalize(self, w):
        self.penalties.append(w)


def mk_eng():
    for f in ("jafj_settings.json", "jafj.db", "jafj_limits.json"):
        if os.path.exists(f):
            os.remove(f)
    eng = m.Engine()
    eng.st.prof("standard")["channel"] = "@std_chan"
    eng.thr["standard"] = FakeThr()
    return eng


hns = {"m": m, "stub_gpr": stub_gpr, "stub_unp": stub_unp,
       "stub_fwe": stub_fwe, "notes": []}
exec(harness, hns)
run_pimc = hns["run_pimc"]


async def scenario_member():
    eng = mk_eng()
    client = FakeClient("member")
    gate = m.CheckGate(base_gap=0.2)
    wf = {"last": 0}
    r = await run_pimc(eng, client, gate, wf, 4242)
    return r, client, gate, wf


r, client, gate, wf = asyncio.run(scenario_member())
check(r is True, "G5: عضو → True")
check(len(client.calls) == 1, "G5: فقط یک درخواست (کانال اول عضو بود)")

# فلود → None + سقف گیت + جریمه تروتیل + لاگ
async def scenario_flood():
    eng = mk_eng()
    client = FakeClient("flood")
    gate = m.CheckGate(base_gap=0.2)
    wf = {"last": 0}
    r = await run_pimc(eng, client, gate, wf, 4242)
    return r, client, gate, wf, eng


r, client, gate, wf, eng = asyncio.run(scenario_flood())
check(r is None, "G5: فلود → None (نامشخص)")
flood_notes = [t for t in hns["notes"] if "FloodWait" in t]
check(flood_notes, "G5: برای فلود ≥۶۰ث به صاحب‌حساب نوتیف رفت")
check(gate.blocked(), "G5: گیت بعد از فلود بسته شد (سقف سراسری)")
check(eng.thr["standard"].penalties == [90],
      "G5: تروتیل ارسال هم جریمه شد (رفتار محافظه‌کارانه قبلی حفظ است)")
kinds = [row["kind"] for row in eng.db.recent(5)]
check("ex_check_flood" in kinds, "G5: فلود چک در events لاگ شد")

# فلود بلند (>45s) → درخواست اصلاً زده نمی‌شود
async def scenario_skip():
    eng = mk_eng()
    client = FakeClient("member")
    gate = m.CheckGate(base_gap=0.2)
    gate.penalize(120, now=time.time())   # سقف ۱۲۰ ثانیه‌ای فعال
    calls0 = len(client.calls)
    r = await run_pimc(eng, client, gate, {"last": 0}, 4242)
    return r, len(client.calls) - calls0


r, made = asyncio.run(scenario_skip())
check(r is None and made == 0,
      "G5: داخل سقف فلود → بدون درخواست، بی‌درنگ None (حلقه‌ها نفس می‌کشند)")

# خطای معمولی → None (مثل قبل)
async def scenario_error():
    eng = mk_eng()
    client = FakeClient("error")
    gate = m.CheckGate(base_gap=0.2)
    r = await run_pimc(eng, client, gate, {"last": 0}, 4242)
    return r


check(asyncio.run(scenario_error()) is None, "G5: خطای API → None مثل قبل")
print("DONE G5 peer_in_my_channel behavioural")


# ════════════════════════════════════════════════════════════
# G6 — حلقه‌ی membership گیت را تغذیه می‌کند (سورس‌پین)
# ════════════════════════════════════════════════════════════
check("check_gate.set_load(" in src and "check_gate.maybe_decay()" in src,
      "G6: حلقه‌ی membership هر دور set_load + maybe_decay می‌زند")
check(src.find("check_gate = CheckGate()") < src.find("async def peer_in_my_channel"),
      "G6: گیت قبل از تعریف تابع چک ساخته شده")
check(src.count("check_gate.wait()") >= 1 and "check_gate.record()" in src,
      "G6: هر درخواست عضویت از wait/record گیت می‌گذرد")
check("check_gate.penalize(w)" in src,
      "G6: فلود چک گیت را جریمه می‌کند (سقف سراسری)")
check("ex_check_flood" in src, "G6: فلود چک لاگ اختصاصی دارد")
print("DONE G6 wiring")


# ════════════════════════════════════════════════════════════
# G7 — نرخ کل: شبیه‌سازی ۲۰ رکورد با set_load → در دقیقه حداکثر ~۸ چک
# ════════════════════════════════════════════════════════════
gate = m.CheckGate(base_gap=1.5)
gate.set_load(20)
now = T
made = 0
for _ in range(500):                     # سعی برای ۵۰۰ درخواست
    w = gate.wait(now)
    if w > 0:
        now += w                          # «صبر» شبیه‌سازی‌شده
    gate.record(now)
    made += 1
    if now - T >= 60:
        break
check(made <= 11,
      f"G7: با ۲۰ رکورد، در یک دقیقه فقط {made} درخواست (قبلاً ۶۰–۱۲۰ بود)")
print("DONE G7 rate math")


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
    env["PORT"] = "8218"
    env["JAFJ_PORT"] = "8218"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- check gate: anti-flood membership checks ---")
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
