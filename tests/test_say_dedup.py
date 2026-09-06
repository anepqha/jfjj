#!/usr/bin/env python3
"""Manager.say anti-duplicate cache (crash-loop fix on Railway).

The dedup cache used to be stored on ``self.say`` — a *bound method*.
A fresh method object is built on every attribute access, so
``hasattr(self.say, "_last")`` was always False and
``self.say._last = {}`` raised::

    AttributeError: 'method' object has no attribute '_last'

The very first message killed the process -> Railway crash-loop.

This test:
  1. rebuilds the pre-fix source (buggy block swapped back in) and shows
     the exact AttributeError from the deploy log;
  2. runs the real Manager with a fake bot against the current source:
       - first send goes out,
       - the same text to the same user inside the 2s window is dropped,
       - the window reopening after 2s sends again,
       - a different text / different user is NOT deduped,
       - send errors never escape say() (retry, then False),
       - the cache lives on the instance (m._say_last), not on the method.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.join(tempfile.gettempdir(), "jafj_say_dedup_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── the fixed block (must exist in the current source) ──
FIXED_BLOCK = '''        last = getattr(self, "_say_last", None)
        if last is None:
            last = self._say_last = {}
        last_send = last.get(msg_key, 0)
        if now_t - last_send < 2:
            return True
        last[msg_key] = now_t
        if len(last) > 500:
            cutoff = now_t - 30
            self._say_last = {k: v for k, v in last.items() if v > cutoff}'''

FIXED_INIT = '''        self._say_last = {}      # کش ضد تکرار say (روی self نه روی bound method)'''

# ── the pre-fix block (from the Railway deploy log) ──
BUGGY_BLOCK = '''        if not hasattr(self.say, "_last"):
            self.say._last = {}
        last_send = self.say._last.get(msg_key, 0)
        if now_t - last_send < 2:
            return True
        self.say._last[msg_key] = now_t
        if len(self.say._last) > 500:
            cutoff = now_t - 30
            self.say._last = {k: v for k, v in self.say._last.items() if v > cutoff}'''

# ── minimal fake telethon, enough for `import manager_82` ──
FAKE_TELETHON = textwrap.dedent('''
    class TelegramClient:
        def __init__(self, session, api_id, api_hash, **kw):
            pass
        async def connect(self):
            return
        async def is_user_authorized(self):
            return True
        async def start(self, bot_token=None):
            return self
        async def disconnect(self):
            return
        async def get_me(self):
            class U:
                username = "fakebot"
                id = 1
            return U()
        def on(self, *a, **k):
            def deco(fn):
                return fn
            return deco
        async def run_until_disconnected(self):
            return
        async def send_message(self, *a, **k):
            class R:
                id = 1
            return R()
        async def get_entity(self, x):
            class E:
                username = None
            return E()
        async def send_file(self, *a, **k):
            return None
        def iter_messages(self, *a, **k):
            async def _gen():
                return
                yield
            return _gen()
        async def download_media(self, *a, **k):
            return None

    class Button:
        @staticmethod
        def inline(*a, **k):
            return ("inline", a, k)
        @staticmethod
        def url(*a, **k):
            return ("url", a, k)
        @staticmethod
        def request_phone(*a, **k):
            return ("phone", a, k)
        @staticmethod
        def text(*a, **k):
            return ("text", a, k)
        @staticmethod
        def clear(*a, **k):
            return None

    class events:
        class NewMessage:
            def __init__(self, *a, **k):
                pass
        class CallbackQuery:
            def __init__(self, *a, **k):
                pass

    class _Err(Exception):
        pass
    FloodWaitError = _Err
    PhoneNumberBannedError = _Err
    PhoneNumberInvalidError = _Err
    SessionPasswordNeededError = _Err
    PhoneCodeInvalidError = _Err
    PhoneCodeExpiredError = _Err
    UserNotParticipantError = _Err
''')

FAKE_SESSIONS = textwrap.dedent('''
    class StringSession:
        def __init__(self, s=""):
            self._s = s or ""
            self.dc_id = 1
            self.server_address = "127.0.0.1"
            self.port = 443
            self.auth_key = b"fake-auth-key-1234567890"
        def save(self):
            return self._s

    class SQLiteSession:
        def __init__(self, *a, **k):
            pass
        def set_dc(self, *a, **k):
            pass
        def save(self):
            pass
        def close(self):
            pass
''')

FAKE_ERRORS = textwrap.dedent('''
    class FloodWaitError(Exception):
        def __init__(self, seconds=0, *a, **k):
            super().__init__(f"FloodWait {seconds}")
            self.seconds = int(seconds or 0)

    class PhoneNumberBannedError(Exception):
        pass
    class PhoneNumberInvalidError(Exception):
        pass
    class SessionPasswordNeededError(Exception):
        pass
    class PhoneCodeInvalidError(Exception):
        pass
    class PhoneCodeExpiredError(Exception):
        pass
    class UserNotParticipantError(Exception):
        pass
''')

# ── inner script: drive the REAL Manager with a fake bot ──
INNER = textwrap.dedent('''
    import os, sys, asyncio, time
    sys.path.insert(0, os.getcwd())
    import manager_82 as M

    class FakeMsg:
        def __init__(self, i):
            self.id = 1000 + i

    class FakeBot:
        """records every send; can be told to fail always or once."""
        def __init__(self):
            self.sent = []          # (uid, text, buttons)
            self.fail_mode = ""     # "" | "always" | "first"
            self._failed = set()    # (uid, text) pairs already failed once
        async def send_message(self, uid, text, parse_mode="html",
                               link_preview=False, buttons=None):
            if self.fail_mode == "always":
                raise RuntimeError("send exploded")
            if self.fail_mode == "first" and (uid, text) not in self._failed:
                self._failed.add((uid, text))
                raise RuntimeError("first attempt fails")
            self.sent.append((uid, text, buttons))
            return FakeMsg(len(self.sent))

    async def main():
        # control the clock used by manager_82 (M.time is the stdlib module)
        real_time = time.time
        fake_now = [real_time()]
        time.time = lambda: fake_now[0]

        m = M.Manager()
        bot = FakeBot()
        m.bot = bot
        results = []

        # cache lives on the instance, not on the bound method
        on_instance = isinstance(getattr(m, "_say_last", None), dict)
        results.append(("cache on instance (m._say_last)", on_instance))

        # 1) first send goes out
        r1 = await m.say(11, "hello")
        results.append(("first send returns True", r1 is True))
        results.append(("first send delivered", len(bot.sent) == 1
                        and bot.sent[0][0] == 11 and bot.sent[0][1] == "hello"))

        # 2) same text, same user, inside 2s -> dropped (True, no new send)
        r2 = await m.say(11, "hello")
        results.append(("dup dropped, returns True", r2 is True))
        results.append(("dup not re-sent", len(bot.sent) == 1))

        # 3) window reopened after >2s -> sent again
        fake_now[0] += 2.5
        r3 = await m.say(11, "hello")
        results.append(("after 2s window reopens, sent again",
                        r3 is True and len(bot.sent) == 2))

        # 4) different text is NOT deduped
        r4 = await m.say(11, "other text")
        results.append(("different text sent", r4 is True and len(bot.sent) == 3
                        and bot.sent[-1][1] == "other text"))

        # 5) different user is NOT deduped (same text)
        r5 = await m.say(22, "hello")
        results.append(("different user sent", r5 is True and len(bot.sent) == 4
                        and bot.sent[-1][0] == 22))

        # 6) buttons path works
        r6 = await m.say(33, "with buttons", buttons=[("b", "cb")])
        results.append(("buttons path ok", r6 is True and len(bot.sent) == 5
                        and bot.sent[-1][2] == [("b", "cb")]))

        # 7) send error must never escape say(): retry, then False
        fake_now[0] += 3.0
        bot.fail_mode = "always"
        raised = False
        r7 = None
        try:
            r7 = await m.say(44, "will fail")
        except Exception:
            raised = True
        results.append(("permanent error: no exception", not raised))
        results.append(("permanent error: returns False", r7 is False))

        # 8) transient error: first attempt fails, retry (no buttons) delivers
        fake_now[0] += 3.0
        bot.fail_mode = "first"
        bot.sent = []
        raised2 = False
        r8 = None
        try:
            r8 = await m.say(55, "retry me")
        except Exception:
            raised2 = True
        results.append(("transient error: no exception", not raised2))
        results.append(("transient error: retry delivers",
                        r8 is True and len(bot.sent) == 1))

        time.time = real_time

        print(f"BUILD {M.BUILD_VERSION}")
        for name, ok in results:
            print(f"CHECK {'PASS' if ok else 'FAIL'} - {name}")
        bad = [name for name, ok in results if not ok]
        print("FIXED_RESULT", "PASS" if not bad else "FAIL")

    asyncio.run(main())
''')

# ── inner script: same flow against the RECONSTRUCTED PRE-FIX source ──
INNER_BUGGY = textwrap.dedent('''
    import os, sys, asyncio
    sys.path.insert(0, os.getcwd())
    import manager_82 as M

    async def main():
        class FakeBot:
            async def send_message(self, *a, **k):
                class R:
                    id = 1
                return R()

        m = M.Manager()
        m.bot = FakeBot()
        try:
            await m.say(11, "hello")
            print("BUG_DEMO FAIL - no exception raised")
        except AttributeError as e:
            msg = str(e)
            print(f"AttributeError: {msg}")
            ok = "'method' object has no attribute '_last'" in msg
            print("BUG_DEMO", "PASS" if ok else "FAIL")
        except Exception as e2:
            print("BUG_DEMO FAIL - wrong exception:", type(e2).__name__, e2)

    asyncio.run(main())
''')


def reset():
    """Fresh APP with the current source + fake telethon."""
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(APP, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    for f in ("manager_82.py", "95.py"):
        shutil.copy2(os.path.join(SRC_REPO, f), APP)
    tel = os.path.join(APP, "telethon")
    os.makedirs(tel, exist_ok=True)
    with open(os.path.join(tel, "__init__.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_TELETHON)
    with open(os.path.join(tel, "sessions.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_SESSIONS)
    with open(os.path.join(tel, "errors.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_ERRORS)


def run_inner(inner, port):
    env = dict(os.environ)
    env["DATA_DIR"] = DATA
    env["PORT"] = str(port)
    env["JAFJ_PORT"] = str(port)
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID",
              "JAFJ_HOSTED", "JAFJ_LOGIN_RETRY", "BACKUP_CHAT", "BACKUP_EVERY",
              "BOT_TOKEN", "API_ID", "API_HASH"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", inner], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=60)
    return p.stdout + p.stderr


def make_buggy_copy():
    """Swap the fixed block back in -> faithful pre-fix manager_82.py."""
    src_path = os.path.join(APP, "manager_82.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    if src.count(FIXED_BLOCK) != 1:
        return False, "fixed dedup block not found (was the fix reverted?)"
    if src.count(FIXED_INIT) != 1:
        return False, "self._say_last init line not found (was the fix reverted?)"
    src = src.replace(FIXED_BLOCK, BUGGY_BLOCK, 1)
    src = src.replace("\n" + FIXED_INIT, "", 1)
    with open(src_path, "w", encoding="utf-8") as f:
        f.write(src)
    return True, "pre-fix source reconstructed"


def test_prefix_shows_bug():
    print("--- 1: pre-fix source crashes with the exact deploy-log error ---")
    reset()
    ok_make, why = make_buggy_copy()
    if not ok_make:
        print("BUGGY-BUILD FAIL -", why)
        return False
    out = run_inner(INNER_BUGGY, 8161)
    print("\n".join(l for l in out.splitlines()
                    if ("BUG_DEMO" in l or "AttributeError" in l)) or out[-2000:])
    good = "BUG_DEMO PASS" in out
    print("pre-fix-demo:", "PASS" if good else "FAIL")
    if not good:
        print(out[-3000:])
    return good


def test_fixed_behaviour():
    print("--- 2: fixed source: dedup works, errors never escape say() ---")
    reset()
    out = run_inner(INNER, 8162)
    checks = [l for l in out.splitlines() if l.startswith("CHECK")]
    print("\n".join(checks) or out[-2000:])
    print("\n".join(l for l in out.splitlines()
                    if l.startswith(("BUILD", "FIXED_RESULT"))))
    good = "FIXED_RESULT PASS" in out and not any(" FAIL " in c or c.endswith("FAIL") for c in checks)
    print("fixed-behaviour:", "PASS" if good else "FAIL")
    if not good:
        print(out[-3000:])
    return good


if __name__ == "__main__":
    ok = test_prefix_shows_bug() and test_fixed_behaviour()
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
