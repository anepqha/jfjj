#!/usr/bin/env python3
"""login_bot: boot1 FloodWait then success + session saved; boot2 no start()."""
import os, sys, shutil, subprocess, tempfile, textwrap

ROOT = os.path.join(tempfile.gettempdir(), "jafj_login_run")
APP = os.path.join(ROOT, "app")
DATA = os.path.join(ROOT, "data")
SRC_REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_INIT = textwrap.dedent('''
import os
START_CALLS = []
CONNECT_CALLS = []
AUTH_CALLS = []

class TelegramClient:
    def __init__(self, session, api_id, api_hash, **kw):
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self._connected = False
    async def connect(self):
        CONNECT_CALLS.append(1)
        # record to file for outer check (subprocess)
        try:
            with open(os.environ.get("FAKE_CALL_LOG", "/tmp/fake_calls.log"), "a") as f:
                f.write("connect\\n")
        except Exception:
            pass
        self._connected = True
    async def is_user_authorized(self):
        AUTH_CALLS.append(1)
        try:
            with open(os.environ.get("FAKE_CALL_LOG", "/tmp/fake_calls.log"), "a") as f:
                f.write("auth\\n")
        except Exception:
            pass
        try:
            s = getattr(self.session, "_s", "") or ""
            # corrupted marker
            if s == "CORRUPTED":
                return False
            return bool(s and len(s) > 5)
        except Exception:
            return False
    async def start(self, bot_token=None):
        START_CALLS.append(bot_token)
        try:
            with open(os.environ.get("FAKE_CALL_LOG", "/tmp/fake_calls.log"), "a") as f:
                f.write("start\\n")
        except Exception:
            pass
        # first start fails with FloodWait if env says so
        try:
            n = len(START_CALLS)
        except Exception:
            n = 1
        if os.environ.get("FAKE_FLOOD_FIRST", "") == "1" and n == 1:
            from telethon.errors import FloodWaitError
            secs = int(os.environ.get("FAKE_FLOOD_SECONDS", "7") or "7")
            raise FloodWaitError(secs)
        # success: set session string
        try:
            self.session._s = "FAKEBOTSESSION_" + "X"*40
        except Exception:
            pass
        self._connected = True
        return self
    async def disconnect(self):
        self._connected = False
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
''')

FAKE_SESSIONS = textwrap.dedent('''
class StringSession:
    def __init__(self, s=""):
        if s == "CORRUPTED":
            raise ValueError("corrupted session string")
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

INNER_BOOT1 = textwrap.dedent('''
import os, sys, asyncio
sys.path.insert(0, os.getcwd())
os.environ["PORT"] = os.environ.get("FAKE_PORT", "8141")
import manager_82 as M

async def main():
    sleeps = []
    orig_sleep = asyncio.sleep
    async def fake_sleep(s):
        sleeps.append(float(s))
        return
    asyncio.sleep = fake_sleep
    try:
        m = M.Manager()
        # ensure no leftover session
        # (outer already cleaned DATA)
        client = await m.login_bot(tries=5)
        # restore
        asyncio.sleep = orig_sleep
        import telethon
        n_start = len(telethon.START_CALLS)
        sess_path = M.BOT_SESSION_FILE
        exists = os.path.isfile(sess_path)
        sz = os.path.getsize(sess_path) if exists else 0
        print(f"BOOT1 start_calls={n_start} sleeps={sleeps} sess_exists={exists} sess_size={sz}")
        # expect: first FloodWait 7 -> sleep 37, second success
        ok = (n_start == 2 and exists and sz > 10 and len(sleeps) >= 1 and abs(sleeps[0] - 37) < 0.01)
        print("BOOT1_RESULT", "PASS" if ok else "FAIL")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("BOOT1_RESULT FAIL", type(e).__name__, e)

asyncio.run(main())
''')

INNER_BOOT2 = textwrap.dedent('''
import os, sys, asyncio
sys.path.insert(0, os.getcwd())
os.environ["PORT"] = os.environ.get("FAKE_PORT", "8142")
import manager_82 as M

async def main():
    sleeps = []
    orig_sleep = asyncio.sleep
    async def fake_sleep(s):
        sleeps.append(float(s))
        return
    asyncio.sleep = fake_sleep
    try:
        m = M.Manager()
        client = await m.login_bot(tries=5)
        asyncio.sleep = orig_sleep
        import telethon
        n_start = len(telethon.START_CALLS)
        n_conn = len(telethon.CONNECT_CALLS)
        n_auth = len(telethon.AUTH_CALLS)
        print(f"BOOT2 start_calls={n_start} connect={n_conn} auth={n_auth} sleeps={sleeps}")
        ok = (n_start == 0 and n_conn >= 1 and n_auth >= 1)
        print("BOOT2_RESULT", "PASS" if ok else "FAIL")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("BOOT2_RESULT FAIL", type(e).__name__, e)

asyncio.run(main())
''')

INNER_CORRUPT = textwrap.dedent('''
import os, sys, asyncio
sys.path.insert(0, os.getcwd())
os.environ["PORT"] = os.environ.get("FAKE_PORT", "8143")
import manager_82 as M

async def main():
    orig_sleep = asyncio.sleep
    async def fake_sleep(s):
        return
    asyncio.sleep = fake_sleep
    try:
        # write corrupted session
        with open(M.BOT_SESSION_FILE, "w", encoding="utf-8") as f:
            f.write("CORRUPTED")
        m = M.Manager()
        # disable flood for this run (should go token path directly)
        client = await m.login_bot(tries=5)
        asyncio.sleep = orig_sleep
        import telethon
        n_start = len(telethon.START_CALLS)
        with open(M.BOT_SESSION_FILE, encoding="utf-8") as f:
            content = f.read().strip()
        ok = (n_start >= 1 and content != "CORRUPTED" and len(content) > 10)
        print(f"CORRUPT start_calls={n_start} content_len={len(content)}")
        print("CORRUPT_RESULT", "PASS" if ok else "FAIL")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("CORRUPT_RESULT FAIL", type(e).__name__, e)

asyncio.run(main())
''')

def reset_app():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(APP, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    for f in ("manager_82.py", "95.py"):
        shutil.copy2(os.path.join(SRC_REPO, f), APP)
    # fake telethon inside APP (shadows real)
    tel = os.path.join(APP, "telethon")
    os.makedirs(tel, exist_ok=True)
    open(os.path.join(tel, "__init__.py"), "w", encoding="utf-8").write(FAKE_INIT)
    open(os.path.join(tel, "sessions.py"), "w", encoding="utf-8").write(FAKE_SESSIONS)
    open(os.path.join(tel, "errors.py"), "w", encoding="utf-8").write(FAKE_ERRORS)

def run_inner(inner, port, flood_first="1", flood_secs="7"):
    env = dict(os.environ)
    env["DATA_DIR"] = DATA
    env["PORT"] = str(port)
    env["FAKE_PORT"] = str(port)
    env["FAKE_FLOOD_FIRST"] = flood_first
    env["FAKE_FLOOD_SECONDS"] = flood_secs
    env["FAKE_CALL_LOG"] = os.path.join(ROOT, f"calls_{port}.log")
    # clean backup/config env
    for k in ("BACKUP_CHAT", "BACKUP_EVERY", "BOT_TOKEN", "API_ID", "API_HASH"):
        env.pop(k, None)
    # ensure call log fresh
    try:
        os.remove(env["FAKE_CALL_LOG"])
    except Exception:
        pass
    p = subprocess.run([sys.executable, "-c", inner], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=40)
    return p.stdout + p.stderr

def test_boot1():
    print("--- 1: boot1 FloodWait then success + session saved ---")
    reset_app()
    out = run_inner(INNER_BOOT1, 8141, "1", "7")
    print("\n".join([l for l in out.splitlines() if "BOOT1" in l] ) or out[-2000:])
    good = "BOOT1_RESULT PASS" in out
    print("boot1:", "PASS" if good else "FAIL")
    if not good:
        print(out[-3000:])
    return good

def test_boot2():
    print("--- 2: boot2 uses saved session, no start() ---")
    # do NOT reset DATA (keep session from boot1), but ensure APP/fake still there
    # APP/DATA already from boot1; just run boot2
    out = run_inner(INNER_BOOT2, 8142, "1", "7")
    print("\n".join([l for l in out.splitlines() if "BOOT2" in l] ) or out[-2000:])
    good = "BOOT2_RESULT PASS" in out
    print("boot2:", "PASS" if good else "FAIL")
    if not good:
        print(out[-3000:])
    return good

def test_corrupt():
    print("--- 3: corrupted saved session is cleared, token path used ---")
    out = run_inner(INNER_CORRUPT, 8143, "0", "7")
    print("\n".join([l for l in out.splitlines() if "CORRUPT" in l] ) or out[-2000:])
    good = "CORRUPT_RESULT PASS" in out
    print("corrupt:", "PASS" if good else "FAIL")
    if not good:
        print(out[-3000:])
    return good

if __name__ == "__main__":
    ok1 = test_boot1()
    ok2 = test_boot2() if ok1 else False
    ok3 = test_corrupt() if ok1 else False
    ok = ok1 and ok2 and ok3
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
