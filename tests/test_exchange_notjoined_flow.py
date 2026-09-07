#!/usr/bin/env python3
"""«جوین شدم» ولی عضو نیست → دو «نیومدی» فاصله‌دار → لفت.

The old handler had two bugs the owner complained about:

  1. When the peer claimed ``جوین شدم`` but was NOT a member, the bot
     replied ``نیومدی`` (msg_no) and then — in the very same breath —
     also said ``جوین شدم`` (msg_ok), back to back, before it had even
     joined anything. The success text must only ever be sent by
     ``reply_joined`` AFTER a real join.

  2. The follow-up «نیومدی» reminders were spaced 5–15 s apart (and
     sometimes fired back to back through the strike path), with only
     one reminder by default and no leave afterwards.

The new behaviour this test pins down:

  * first «نیومدی» goes out immediately (as a reply to the claim),
    with the peer's registered channel written BELOW the text;
  * the SECOND «نیومدی» comes after a random gap — default 20–40 s —
    never back to back, and never a third one;
  * after both reminders, if the peer still has not joined, the bot
    LEAVES the peer's channel (or cancels the exchange if it had not
    joined it yet);
  * if the msg_no text was never configured, the default is «نیومدی»;
  * defaults: max_reminders=2, reminder gap 20–40 s random.

The reminder-loop block is extracted from the real 95.py source and
re-run against a real Engine + real DB with stubbed Telegram calls,
so the leave/approve transitions are tested behaviourally.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(tempfile.gettempdir(), "jafj_exchange_notjoined_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = REPO


DRIVER = r"""
import asyncio, importlib.util, json, os, random, sys, time
sys.path.insert(0, os.getcwd())

spec = importlib.util.spec_from_file_location("m95", "95.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

src = open("95.py", encoding="utf-8").read()

# ── 1. Defaults: two reminders, 20–40 s random gap ─────────────────
eng = m.Engine()
x = eng.ex_cfg()
print("DEF max_reminders", x["max_reminders"])
print("DEF reminder_min", x["reminder_min_sec"])
print("DEF reminder_max", x["reminder_max_sec"])
assert x["max_reminders"] == 2, x["max_reminders"]
assert x["reminder_min_sec"] == 20, x["reminder_min_sec"]
assert x["reminder_max_sec"] == 40, x["reminder_max_sec"]
print("OK defaults")

# ── 2. msg_no default text: «نیومدی» when not configured ──────────
print("DEF_MSG_NO", m.DEFAULT_MSG_NO)
assert m.DEFAULT_MSG_NO == "نیومدی"
print("RENDER msg_no unset ->", repr(eng.ex_render("msg_no")))
assert eng.ex_render("msg_no") == "نیومدی"
assert eng.ex_render("msg_no", "ali", "@chan") == "نیومدی"
# custom text wins; {channel} placeholder still works
x["msg_no"] = "سفارشی {channel}"
print("RENDER msg_no custom ->", repr(eng.ex_render("msg_no", "ali", "@chan")))
assert eng.ex_render("msg_no", "ali", "@chan") == "سفارشی @chan"
# other keys stay silent when unset (no default creep)
assert eng.ex_render("msg_wait") == ""
assert eng.ex_render("msg_ok") == ""
x["msg_no"] = ""
print("OK msg_no default render")

# ── 3. reminder_delay formula in 95.py samples inside 20–40 ────────
# (the inner function is replicated with the same fallbacks; a source
#  check below additionally pins the fallback values themselves)
def reminder_delay():
    lo = max(1, int(x.get("reminder_min_sec", 20) or 20))
    hi = max(lo, int(x.get("reminder_max_sec", 40) or 40))
    return random.randint(lo, hi)
samples = [reminder_delay() for _ in range(300)]
print("SAMPLE reminder min", min(samples), "max", max(samples))
assert all(20 <= v <= 40 for v in samples), "reminder gap out of 20-40"
assert min(samples) < max(samples), "reminder gap not random"
assert 'x.get("reminder_min_sec", 20) or 20' in src
assert 'x.get("reminder_max_sec", 40) or 40' in src
print("OK reminder delay 20-40 random")

# ── 4. Migration of old settings to the new defaults ───────────────
old_cfg = {"exchange": {
    "enabled": True,
    "max_reminders": 1,
    "reminder_min_sec": 5,
    "reminder_max_sec": 15,
    "msg_no": "اول عضو شو",
}}
with open(m.SETTINGS_FILE, "w", encoding="utf-8") as f:
    json.dump(old_cfg, f, ensure_ascii=False)
st = m.Settings()
ox = st.data["exchange"]
print("MIG max_reminders", ox["max_reminders"], "gap",
      ox["reminder_min_sec"], "-", ox["reminder_max_sec"], "msg_no", repr(ox["msg_no"]))
assert ox["max_reminders"] == 2, ox["max_reminders"]
assert ox["reminder_min_sec"] == 20 and ox["reminder_max_sec"] == 40
# the legacy literal default is wiped and falls back to «نیومدی»
assert ox["msg_no"] == ""
mig_eng = m.Engine()
assert mig_eng.ex_render("msg_no") == "نیومدی"
os.remove(m.SETTINGS_FILE)
print("OK migration")

# ── 5. Source shape: not-member branch never says «جوین شدم» ──────
i = src.find("# ── عضو نیست → «نیومدی»")
j = src.find("# ── نامشخص ──", i)
assert i > 0 and j > i, "member-is-False branch markers missing"
branch = src[i:j]
assert "msg_ok" not in branch, "not-member branch still sends msg_ok back to back"
assert 'say("msg_no", link)' in branch, "msg_no no longer carries the peer link"
assert "reminder_delay()" in branch, "reminder schedule no longer random-gapped"
print("OK not-member branch: no msg_ok, link passed, random gap")

# member-None branch must not send msg_ok before a real join either
i2 = src.find("# ── نامشخص ──")
j2 = src.find("# ── عضو هست →", i2)
branch2 = src[i2:j2]
assert "msg_ok" not in branch2, "unknown-membership branch still sends msg_ok early"
print("OK unknown branch: no early msg_ok")

# the reminder queue must also drive already-joined (initiate) records
k = src.find("def ex_reminder_due")
assert k > 0
frag = src[k:k + 600]
assert "'pending','joined'" in frag, "ex_reminder_due does not include joined records"
print("OK ex_reminder_due includes joined")

# ── 6. Behavioural: send_not_joined_reminder body ──────────────────
# Extract the real function and run it with a real Engine and a fake
# client: the registered channel must appear BELOW the text.
a = src.find("    async def send_not_joined_reminder")
b = src.find("    # ---------- پیدا کردن کانال طرف", a)
assert a > 0 and b > a, "send_not_joined_reminder not found"
fn_src = "\n".join(ln[4:] if ln.startswith("    ") else ln
                   for ln in src[a:b].splitlines())

class FakeClient:
    def __init__(self):
        self.sent = []
    async def send_message(self, chat, body, reply_to=None, link_preview=False):
        self.sent.append((chat, body, reply_to))

fake = FakeClient()
ns = {"eng": eng, "client": fake, "DRY_RUN": m.DRY_RUN}
exec(fn_src, ns)
send_rem = ns["send_not_joined_reminder"]

# a real DB record with a registered channel and a claim message
rec, _new = eng.db.ex_add(9001, "@liar", "@theirchan")
eng.db.ex_set(rec["id"], src_chat=111, src_msg=222)

# unset text → default «نیومدی» + channel below
ok = asyncio.run(send_rem(eng.db.ex_get(rec["id"])))
print("BODY default ->", repr(fake.sent[-1][1]))
assert ok is True
assert fake.sent[-1] == (111, "نیومدی\n@theirchan", 222)

# custom text without {channel} → text + channel below
x["msg_no"] = "هنوز عضو نشدی برو جوین شو"
ok = asyncio.run(send_rem(eng.db.ex_get(rec["id"])))
print("BODY custom ->", repr(fake.sent[-1][1]))
assert ok is True
assert fake.sent[-1][1] == "هنوز عضو نشدی برو جوین شو\n@theirchan"

# custom text that already contains the channel → no duplicate line
x["msg_no"] = "هنوز عضو {channel} نشدی"
ok = asyncio.run(send_rem(eng.db.ex_get(rec["id"])))
print("BODY placeholder ->", repr(fake.sent[-1][1]))
assert ok is True
assert fake.sent[-1][1] == "هنوز عضو @theirchan نشدی"
x["msg_no"] = ""
print("OK reminder body: text + channel below")

# ── 6.5 Behavioural: the not-member handler branch itself ─────────
# Extract the real `member is False` branch and run it with a real
# Engine/DB and stubbed Telegram pieces. Guards the headline bug:
# after «نیومدی» there must be NO «جوین شدم», and a re-claim inside
# the active window must not fire a second message back to back.
a = src.find("# ── عضو نیست → «نیومدی»")
b = src.find("# ── نامشخص ──", a)
assert a > 0 and b > a
blk = src[a:b]
blines = blk.splitlines()
while blines and (blines[0].lstrip().startswith("#") or not blines[0].strip()):
    blines.pop(0)
while blines and not blines[-1].strip():
    blines.pop()
ded8 = "\n".join(ln[8:] if ln.startswith("        ") else ln
                 for ln in blines)
branch_src = (
    "async def _blk(eng, x, member, event, sender, sender_name, say,\n"
    "               find_their_channel, reminder_delay, on_exchange_request):\n"
    + "\n".join("    " + ln for ln in ded8.splitlines())
    + "\n"
)
bns = {"time": time}
exec(branch_src, bns)
_blk = bns["_blk"]

class Evt:
    chat_id = 111
    id = 222
class Sender:
    id = 7001
class FnHolder:
    pass

say_calls = []
async def fake_say(key, channel="", fallbacks=()):
    say_calls.append((key, channel))
    return True
async def fake_find(event, sender, chat_id):
    return "@theirchan", "از پیام خودش"
def gap30():
    return 30

holder = FnHolder()
eng2 = m.Engine()
xx = eng2.ex_cfg()
xx["reply"] = True

# first claim: exactly one «نیومدی» with the link, never «جوین شدم»
asyncio.run(_blk(eng2, xx, False, Evt(), Sender(), "tester",
                 fake_say, fake_find, gap30, holder))
print("H1 say_calls", say_calls)
assert say_calls == [("msg_no", "@theirchan")], say_calls
rec7 = eng2.db.ex_by_link("@theirchan")
now = int(time.time())
print("H1 reminders", rec7["reminders"], "status", rec7["status"],
      "next_gap", rec7["next_reminder"] - now)
assert rec7["reminders"] == 1
assert rec7["status"] == "pending"
assert rec7["direction"] == "in"
assert 20 <= rec7["next_reminder"] - now <= 40
assert rec7["src_chat"] == 111 and rec7["src_msg"] == 222
print("OK H1 first claim → one «نیومدی» + link, no «جوین شدم»")

# re-claim while the reminder window is still open: no extra message
nxt_before = rec7["next_reminder"]
asyncio.run(_blk(eng2, xx, False, Evt(), Sender(), "tester",
                 fake_say, fake_find, gap30, holder))
rec7b = eng2.db.ex_get(rec7["id"])
print("H2 say_calls", say_calls, "next_unchanged",
      rec7b["next_reminder"] == nxt_before)
assert say_calls == [("msg_no", "@theirchan")], "back-to-back message on re-claim"
assert rec7b["next_reminder"] == nxt_before, "schedule was pushed/moved"
print("OK H2 re-claim inside window stays silent")

# after the record was left, a new claim starts a fresh round
eng2.db.ex_set(rec7["id"], status="left", next_reminder=0, reminders=2)
asyncio.run(_blk(eng2, xx, False, Evt(), Sender(), "tester",
                 fake_say, fake_find, gap30, holder))
rec7c = eng2.db.ex_get(rec7["id"])
print("H3 say_calls", say_calls, "status", rec7c["status"],
      "reminders", rec7c["reminders"])
assert len(say_calls) == 2 and say_calls[-1] == ("msg_no", "@theirchan")
assert rec7c["status"] == "pending" and rec7c["reminders"] == 1
print("OK H3 new claim after leave → fresh two-message round")
print("OK handler branch behavioural")

# ── 7. Behavioural: the reminder loop itself (real code, real DB) ──
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
    "async def _run(eng, x, stub):\n"
    "    now_rem = int(time.time())\n"
    "    confirm_peer_membership = stub.confirm\n"
    "    send_not_joined_reminder = stub.send_rem\n"
    "    leave_link = stub.leave\n"
    "    reminder_delay = stub.reminder_delay\n"
    "    membership_check_delay = stub.check_delay\n"
    "    note = stub.note\n"
    + "\n".join("    " + ln for ln in ded.splitlines())
    + "\n"
)
hns = {}
exec(harness_src, hns)
run_once = hns["_run"]

class Stub:
    def __init__(self, member_map):
        self.member_map = member_map
        self.sent = []
        self.left = []
        self.notes = []
    async def confirm(self, pid):
        return self.member_map.get(pid)
    async def send_rem(self, rec):
        self.sent.append(rec["id"])
        return True
    async def leave(self, link):
        self.left.append(link)
        return True, ""
    async def note(self, text):
        self.notes.append(text)
    @staticmethod
    def reminder_delay():
        return 30
    @staticmethod
    def check_delay():
        return 25

def mkRec(peer, link, status, reminders, direction="in", due=True):
    r, _n = eng.db.ex_add(peer, f"p{peer}", link)
    eng.db.ex_set(r["id"], status=status, direction=direction,
                  reminders=reminders, peer_id=peer,
                  src_chat=111, src_msg=222,
                  next_reminder=int(time.time()) - 5 if due else 0)
    return eng.db.ex_get(r["id"])

stub = Stub({501: False, 502: False, 503: False, 504: True})
r1 = mkRec(501, "@c501", "pending", 1)     # 2nd reminder due → send + schedule leave-check
r2 = mkRec(502, "@c502", "joined", 2, "out")  # both sent, never came → LEAVE
r3 = mkRec(503, "@c503", "pending", 2)     # both sent, not joined yet → CANCEL
r4 = mkRec(504, "@c504", "pending", 1)     # member now → APPROVE for join
x2 = eng.ex_cfg()
asyncio.run(run_once(eng, x2, stub))

now = int(time.time())
g1 = eng.db.ex_get(r1["id"])
print("S1 sent", stub.sent, "r1 reminders", g1["reminders"], "next", g1["next_reminder"] - now)
assert stub.sent.count(r1["id"]) == 1, "second reminder not sent exactly once"
assert g1["reminders"] == 2
assert 20 <= g1["next_reminder"] - now <= 40, "leave-check gap not 20-40s after 2nd msg"
assert g1["status"] == "pending"
print("OK S1 second reminder spaced 20-40s")

g2 = eng.db.ex_get(r2["id"])
print("S2 left", stub.left, "status", g2["status"])
assert r2["link"] in stub.left, "joined peer that never came was not left"
assert g2["status"] == "left"
assert g2["next_reminder"] == 0
assert x2.get("_scan_now") is True, "out-direction leave did not trigger scan"
assert any("لفت" in n for n in stub.notes), "owner got no leave note"
print("OK S2 leave after two ignored reminders")

g3 = eng.db.ex_get(r3["id"])
print("S3 status", g3["status"], "left", stub.left)
assert g3["status"] == "failed", "never-joined record not cancelled"
assert r3["link"] not in stub.left
print("OK S3 pending exchange cancelled (nothing to leave)")

g4 = eng.db.ex_get(r4["id"])
print("S4 status", g4["status"], "replied", g4["replied"], "next", g4["next_reminder"])
assert g4["status"] == "approved", "member-came record not queued for join"
assert g4["replied"] == 0, "replied flag blocks the post-join «جوین شدم»"
assert g4["next_reminder"] == 0
assert r4["id"] not in stub.sent
print("OK S4 member came → approved, msg_ok possible after real join")

# max_reminders = 0 → silent re-check, no message, no leave
x3 = eng.ex_cfg()
x3["max_reminders"] = 0
r5 = mkRec(505, "@c505", "pending", 0)
stub2 = Stub({505: False})
asyncio.run(run_once(eng, x3, stub2))
g5 = eng.db.ex_get(r5["id"])
print("S5 sent", stub2.sent, "left", stub2.left, "next", g5["next_reminder"] - now)
assert stub2.sent == [] and stub2.left == []
assert g5["status"] == "pending"
assert g5["next_reminder"] > now, "silent monitoring stopped"
print("OK S5 zero-reminders stays silent")

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
    env["PORT"] = "8203"
    env["JAFJ_PORT"] = "8203"
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BOT_TOKEN", "API_ID",
              "API_HASH", "BACKUP_CHAT", "BACKUP_EVERY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode


def main():
    print("--- exchange_notjoined_flow: دو «نیومدی» فاصله‌دار → لفت ---")
    reset()
    out, err, rc = run()
    if err.strip():
        print("--- driver stderr (tail) ---")
        print(err[-2000:])
    print("--- driver output ---")
    for line in out.splitlines():
        if line.startswith(("DEF", "RENDER", "SAMPLE", "BODY", "S1", "S2", "S3",
                            "S4", "S5", "MIG", "OK", "MISSING", "FAIL", "DONE")):
            print(line)
    if "DONE PASS" in out and "FAIL" not in out and rc == 0:
        print("ALL: PASS")
        return True
    print("ALL: FAIL")
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
