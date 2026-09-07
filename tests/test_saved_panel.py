#!/usr/bin/env python3
"""Saved-Messages panel must answer commands the user sends from their phone.

Every message in Saved Messages arrives at the session with out=True —
including the commands the user types from their own phone — because the
sender is the account itself. The old handler was registered as
``events.NewMessage(chats="me", incoming=True)`` and ALSO dropped anything
with out=True, and Telethon's incoming filter does::

    if self.incoming and event.message.out: return   # telethon/events/newmessage.py

so the ".panel" command could NEVER fire — «پنل باز نمی‌شود».

The fix registers the handler for BOTH directions in "me" and skips only
the messages the bot itself sent (tracked by message id) so the panel
output can never be re-processed as a command (no loop).

This test drives the real connect_and_run() with a fake telethon:
  1. the "me" handler is registered for incoming AND outgoing;
  2. an outgoing ".panel" (out=True, like a command from the phone)
     opens the panel;
  3. the panel answer itself (tracked id) is NOT re-processed;
  4. further user commands still work afterwards;
  5. the exchange handler keeps its incoming-only filter (unchanged).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(tempfile.gettempdir(), "jafj_saved_panel_run")

INNER = textwrap.dedent('''
    import asyncio, importlib.util, os, sys, time, types

    REPO = os.environ["REPO"]
    os.chdir(os.environ["DATA"])          # jafj files land here

    # ── fake telethon ──────────────────────────────────────────────
    CLIENTS = []

    class FakeMsg:
        def __init__(self, mid, text=""):
            self.id = mid
            self.raw_text = text

    class FakeClient:
        def __init__(self, *a, **k):
            self.handlers = []          # (builder, fn)
            self.sent = []              # messages sent to "me"
            self._id = 10000
            self._done = asyncio.Event()
            CLIENTS.append(self)
        def on(self, builder):
            def deco(fn):
                self.handlers.append((builder, fn))
                return fn
            return deco
        async def start(self, phone=None, **k):
            return self
        async def get_me(self):
            return types.SimpleNamespace(first_name="Test", username="tester", id=555)
        async def send_message(self, peer, text=None, **k):
            self._id += 1
            msg = FakeMsg(self._id, text or "")
            if str(peer) in ("me", "555"):
                self.sent.append(msg)
            return msg
        async def run_until_disconnected(self):
            await self._done.wait()
        def __getattr__(self, name):
            async def _stub(*a, **k):
                return None
            return _stub

    class NewMsgBuilder:
        def __init__(self, *a, **k):
            self.args, self.kwargs = a, k

    tl = types.ModuleType("telethon")
    tl.TelegramClient = FakeClient
    tl.events = types.SimpleNamespace(NewMessage=NewMsgBuilder)
    tl.errors = types.SimpleNamespace(**{
        n: type(n, (Exception,), {}) for n in (
            "FloodWaitError", "SlowModeWaitError", "ChatWriteForbiddenError",
            "ChannelPrivateError", "UsernameNotOccupiedError", "RPCError",
            "UserNotParticipantError", "UserAlreadyParticipantError",
            "InviteHashExpiredError", "InviteHashInvalidError",
            "ChannelsTooMuchError", "InviteRequestSentError")})
    tlchan = types.ModuleType("telethon.tl.functions.channels")
    tlmsg = types.ModuleType("telethon.tl.functions.messages")
    for n in ("JoinChannelRequest", "LeaveChannelRequest", "GetParticipantRequest"):
        setattr(tlchan, n, type(n, (), {}))
    tlmsg.ImportChatInviteRequest = type("ImportChatInviteRequest", (), {})
    tlf = types.ModuleType("telethon.tl.functions")
    tlf.channels, tlf.messages = tlchan, tlmsg
    tltl = types.ModuleType("telethon.tl")
    tltl.functions = tlf
    tl.tl = tltl
    for name, mod in (("telethon", tl), ("telethon.events", tl.events),
                      ("telethon.errors", tl.errors), ("telethon.tl", tltl),
                      ("telethon.tl.functions", tlf),
                      ("telethon.tl.functions.channels", tlchan),
                      ("telethon.tl.functions.messages", tlmsg)):
        sys.modules[name] = mod

    # ── import the real 95.py ──────────────────────────────────────
    spec = importlib.util.spec_from_file_location(
        "selfbot95", os.path.join(REPO, "95.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DRY_RUN = True

    eng = mod.Engine()
    creds = {"api_id": 1, "api_hash": "h", "phone": "+989120000000"}

    class Ev:
        def __init__(self, text, mid, out=True):
            self.raw_text = text
            self.out = out
            self.message = types.SimpleNamespace(id=mid)
            self.chat_id = 555
        async def get_reply_message(self):
            return None
        async def get_chat(self):
            return types.SimpleNamespace(id=555)
        async def reply(self, t):
            return await CLIENT.send_message("me", t)

    async def main():
        task = asyncio.create_task(mod.connect_and_run(eng, creds))
        global CLIENT
        client = None
        for _ in range(400):                      # wait for handler setup
            if CLIENTS and (client is None or client is not CLIENTS[-1]):
                client = CLIENTS[-1]
            if client is not None and len(client.handlers) >= 2:
                break
            await asyncio.sleep(0.05)
        CLIENT = client
        print("HANDLERS %d" % len(client.handlers))

        me_fn = me_b = ex_b = None
        for b, fn in client.handlers:
            if b.kwargs.get("chats") == "me":
                me_fn, me_b = fn, b
            elif b.kwargs.get("incoming") is True:
                ex_b = b
        print("ME_REGISTER both_dirs=%s chats=%r"
              % (me_b.kwargs.get("incoming") is not True, me_b.kwargs.get("chats")))
        print("EX_INCOMING_kept %s" % (ex_b is not None))

        # 1) ".panel" sent from the user's phone arrives as out=True
        n0 = len(client.sent)
        await me_fn(Ev(".panel", 5001, out=True))
        p = client.sent[n0:]
        print("PANEL opens=%s text_ok=%s"
              % (bool(p), bool(p) and ("جفج" in p[-1].raw_text)))
        panel_id = p[-1].id if p else 0

        # 2) the panel answer itself (tracked id) must not re-trigger
        n1 = len(client.sent)
        await me_fn(Ev(".panel", panel_id, out=True))
        print("NO_LOOP ok=%s" % (len(client.sent) == n1))

        # 3) a fresh user command still works afterwards
        n2 = len(client.sent)
        await me_fn(Ev("پنل", 5002, out=True))
        print("AGAIN ok=%s" % (len(client.sent) > n2))

        client._done.set()
        rc = "ok"
        try:
            rc = await asyncio.wait_for(task, timeout=30)
        except Exception as e:
            rc = "ERR:%r" % (e,)
        print("DONE rc=%s" % rc)

    asyncio.run(main())
''')


def run():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    env = dict(os.environ, REPO=REPO, DATA=ROOT)
    return subprocess.run([sys.executable, "-c", INNER], env=env,
                          text=True, capture_output=True, timeout=180)


def main():
    print("--- saved-messages panel: commands from the user's own phone ---")
    r = run()
    out = r.stdout + r.stderr
    keep = [l for l in r.stdout.splitlines()
            if l.startswith(("HANDLERS ", "ME_REGISTER ", "EX_INCOMING ",
                             "PANEL ", "NO_LOOP ", "AGAIN ", "DONE "))]
    print("\n".join(keep) or out[-2000:])
    good = (r.returncode == 0
            and "ME_REGISTER both_dirs=True chats='me'" in out
            and "EX_INCOMING_kept True" in out
            and "PANEL opens=True text_ok=True" in out
            and "NO_LOOP ok=True" in out
            and "AGAIN ok=True" in out
            and "DONE rc=retry" in out)
    print("\n%s  saved-panel" % ("✅ PASS" if good else "❌ FAIL"))
    if not good and r.returncode != 0:
        print(out[-3000:])
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
