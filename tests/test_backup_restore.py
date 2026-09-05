#!/usr/bin/env python3
"""backup/restore via fake telegram storage (file-based, shared across boots)."""
import os, sys, shutil, subprocess, tempfile, textwrap, zipfile

ROOT = os.path.join(tempfile.gettempdir(), "jafj_backup_run")
APP = os.path.join(ROOT, "app")
DATA1 = os.path.join(ROOT, "data1")
DATA2 = os.path.join(ROOT, "data2")
FAKESTORE = os.path.join(ROOT, "fakestore")
SRC_REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_INIT = textwrap.dedent('''
import os, shutil

class _FakeMsg:
    def __init__(self, caption, fpath):
        self.caption = caption or ""
        self.text = caption or ""
        self._fpath = fpath
        self.id = 1

class TelegramClient:
    def __init__(self, session, api_id, api_hash, **kw):
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
    async def connect(self):
        return
    async def is_user_authorized(self):
        return True
    async def start(self, bot_token=None):
        try:
            self.session._s = "FAKEBOTSESSION_" + "X"*40
        except Exception:
            pass
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
        # public check: @public* is public
        class E:
            username = None
        e = E()
        try:
            s = str(x)
            if s.startswith("@public") or s == "publicchannel":
                e.username = s.lstrip("@")
            elif os.environ.get("FAKE_FORCE_PUBLIC", "") == "1":
                e.username = "somepublic"
        except Exception:
            pass
        return e
    async def send_file(self, target, fpath, caption=None, **kw):
        store = os.environ.get("FAKE_BACKUP_DIR", "/tmp/fakebackup")
        os.makedirs(store, exist_ok=True)
        dst = os.path.join(store, "latest.zip")
        cap = os.path.join(store, "latest.caption")
        tgt = os.path.join(store, "latest.target")
        shutil.copy2(str(fpath), dst)
        with open(cap, "w", encoding="utf-8") as f:
            f.write(str(caption or ""))
        with open(tgt, "w", encoding="utf-8") as f:
            f.write(str(target))
        return _FakeMsg(caption, dst)
    def iter_messages(self, target, limit=50, **kw):
        store = os.environ.get("FAKE_BACKUP_DIR", "/tmp/fakebackup")
        async def _gen():
            try:
                z = os.path.join(store, "latest.zip")
                c = os.path.join(store, "latest.caption")
                if os.path.isfile(z) and os.path.isfile(c):
                    with open(c, encoding="utf-8") as f:
                        cap = f.read()
                    yield _FakeMsg(cap, z)
            except Exception:
                return
        return _gen()
    async def download_media(self, msg, file=None, **kw):
        src = getattr(msg, "_fpath", None)
        if src is None:
            # try latest.zip
            store = os.environ.get("FAKE_BACKUP_DIR", "/tmp/fakebackup")
            src = os.path.join(store, "latest.zip")
        if file is None:
            return src
        shutil.copy2(str(src), str(file))
        return str(file)

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
            raise ValueError("corrupted")
        self._s = s or ""
        self.dc_id = 1
        self.server_address = "127.0.0.1"
        self.port = 443
        self.auth_key = b"fake"
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
class PhoneNumberBannedError(Exception): pass
class PhoneNumberInvalidError(Exception): pass
class SessionPasswordNeededError(Exception): pass
class PhoneCodeInvalidError(Exception): pass
class PhoneCodeExpiredError(Exception): pass
class UserNotParticipantError(Exception): pass
''')

INNER_BOOT1 = textwrap.dedent('''
import os, sys, asyncio, zipfile
sys.path.insert(0, os.getcwd())
import manager_82 as M

async def main():
    try:
        m = M.Manager()
        # 1 client
        if not m.db.get(111):
            m.db.add(111, "testuser", "Test User", 0)
        m.db.set(111, session="SESSION_" + "Y"*60, phone="+989120000000", status="active", phone_verified=1, tg_id=111)
        m.cfg.save()
        with open(M.BOT_SESSION_FILE, "w", encoding="utf-8") as f:
            f.write("FAKEBOTSESSION_" + "X"*40)
        # checkpoint to ensure db file has data
        try:
            m.db.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            m.db.c.commit()
        except Exception:
            pass
        try:
            m.shop.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            m.shop.c.commit()
        except Exception:
            pass
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        m.bot = TelegramClient(StringSession("BOT"), 1, "hash")
        ok, msg = await m.backup_once(force=True)
        print(f"BACKUP1 ok={ok} msg={msg}")
        # verify zip in fakestore
        store = os.environ.get("FAKE_BACKUP_DIR")
        zpath = os.path.join(store, "latest.zip")
        cpath = os.path.join(store, "latest.caption")
        exists = os.path.isfile(zpath)
        cap = open(cpath, encoding="utf-8").read() if os.path.isfile(cpath) else ""
        names = []
        if exists:
            with zipfile.ZipFile(zpath) as z:
                names = sorted(z.namelist())
        print(f"BACKUP1 zip_exists={exists} caption_has_tag={'JAFJBACKUP1' in cap} names={names}")
        # fingerprint: second backup without force should be no-change
        ok2, msg2 = await m.backup_once(force=False)
        print(f"BACKUP1 second ok={ok2} msg={msg2}")
        good = (ok and exists and "JAFJBACKUP1" in cap
                and "manager.db" in names and "shop.db" in names
                and "manager_config.json" in names and "manager_bot.string" in names
                and (not ok2))
        print("BACKUP1_RESULT", "PASS" if good else "FAIL")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("BACKUP1_RESULT FAIL", type(e).__name__, e)

asyncio.run(main())
''')

INNER_BOOT2 = textwrap.dedent('''
import os, sys, asyncio
sys.path.insert(0, os.getcwd())
import manager_82 as M

async def main():
    try:
        # mock prepare to avoid telethon SQLiteSession need (fake has dummy, but session short? use long)
        # Our fake SQLiteSession is dummy so real prepare would "work" but write dummy? Actually Supervisor.write_session uses StringSession+SQLiteSession.
        # Fake StringSession requires len>5, our session is long, so ok. Fake SQLiteSession does nothing, so no file created!
        # That means regen in restore would not create jafj.session file. For test we only check DB count + files, not jafj.session.
        # So fine. But to avoid error, we keep as is.
        m = M.Manager()
        try:
            cnt0 = m.db.x("SELECT COUNT(*) c FROM clients", (), "one")
            n0 = cnt0["c"] if cnt0 else -1
        except Exception as e:
            n0 = -99
        print(f"RESTORE0 count={n0}")
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        m.bot = TelegramClient(StringSession("BOT2"), 1, "hash")
        ok, msg = await m.restore_from_backup(force=False)
        print(f"RESTORE1 ok={ok} msg={msg}")
        try:
            cnt1 = m.db.x("SELECT COUNT(*) c FROM clients", (), "one")
            n1 = cnt1["c"] if cnt1 else -1
        except Exception as e:
            n1 = -99
        exists_string = os.path.isfile(M.BOT_SESSION_FILE)
        exists_config = os.path.isfile(M.CONFIG_FILE)
        sz_string = os.path.getsize(M.BOT_SESSION_FILE) if exists_string else 0
        print(f"RESTORE1 count={n1} string={exists_string}:{sz_string} config={exists_config}")
        good = (n0 == 0 and ok and n1 == 1 and exists_string and sz_string > 10 and exists_config)
        print("RESTORE_RESULT", "PASS" if good else "FAIL")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("RESTORE_RESULT FAIL", type(e).__name__, e)

asyncio.run(main())
''')

INNER_PUBLIC = textwrap.dedent('''
import os, sys, asyncio
sys.path.insert(0, os.getcwd())
import manager_82 as M
async def main():
    try:
        m = M.Manager()
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        m.bot = TelegramClient(StringSession("BOT"), 1, "hash")
        ok, msg = await m.backup_once(force=True)
        print(f"PUBLIC ok={ok} msg={msg}")
        good = (not ok and "عمومی" in msg)
        print("PUBLIC_RESULT", "PASS" if good else "FAIL")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("PUBLIC_RESULT FAIL", e)
asyncio.run(main())
''')

def reset_all():
    shutil.rmtree(ROOT, ignore_errors=True)
    for d in (APP, DATA1, DATA2, FAKESTORE):
        os.makedirs(d, exist_ok=True)
    for f in ("manager_82.py", "95.py"):
        shutil.copy2(os.path.join(SRC_REPO, f), APP)
    tel = os.path.join(APP, "telethon")
    os.makedirs(tel, exist_ok=True)
    open(os.path.join(tel, "__init__.py"), "w", encoding="utf-8").write(FAKE_INIT)
    open(os.path.join(tel, "sessions.py"), "w", encoding="utf-8").write(FAKE_SESSIONS)
    open(os.path.join(tel, "errors.py"), "w", encoding="utf-8").write(FAKE_ERRORS)

def run_inner(inner, data_dir, port, backup_chat):
    env = dict(os.environ)
    env["DATA_DIR"] = data_dir
    env["PORT"] = str(port)
    env["FAKE_BACKUP_DIR"] = FAKESTORE
    env["BACKUP_CHAT"] = backup_chat
    env.pop("BACKUP_EVERY", None)
    for k in ("BOT_TOKEN", "API_ID", "API_HASH"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", inner], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=40)
    return p.stdout + p.stderr

def test_boot1():
    print("--- 1: boot1 backup sends zip ---")
    out = run_inner(INNER_BOOT1, DATA1, 8151, "12345")
    print("\n".join([l for l in out.splitlines() if "BACKUP1" in l]) or out[-3000:])
    good = "BACKUP1_RESULT PASS" in out
    print("boot1:", "PASS" if good else "FAIL")
    if not good:
        print(out[-4000:])
    return good

def test_boot2():
    print("--- 2: boot2 empty DB restores 1 client + files ---")
    out = run_inner(INNER_BOOT2, DATA2, 8152, "12345")
    print("\n".join([l for l in out.splitlines() if "RESTORE" in l]) or out[-3000:])
    good = "RESTORE_RESULT PASS" in out
    print("boot2:", "PASS" if good else "FAIL")
    if not good:
        print(out[-4000:])
    return good

def test_public():
    print("--- 3: public channel rejected ---")
    # use DATA1 (has data) but with public chat
    out = run_inner(INNER_PUBLIC, DATA1, 8153, "@publicchannel")
    print("\n".join([l for l in out.splitlines() if "PUBLIC" in l]) or out[-3000:])
    good = "PUBLIC_RESULT PASS" in out
    print("public:", "PASS" if good else "FAIL")
    if not good:
        print(out[-4000:])
    return good

if __name__ == "__main__":
    reset_all()
    ok1 = test_boot1()
    ok2 = test_boot2() if ok1 else False
    ok3 = test_public()
    ok = ok1 and ok2 and ok3
    print("ALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
