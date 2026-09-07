#!/usr/bin/env python3
"""B-series: graceful shutdown / task-cancel behaviour of connect_and_run().

These scenarios reproduce the two ways the shutdown of the selfbot used to
go wrong:

  * the code side — the ``finally`` cleanup inside ``connect_and_run`` used
    ``except (asyncio.CancelledError, Exception): pass`` around the cleanup
    awaits, which silently swallowed an *outer* cancellation that arrived
    while shutting down, so the coroutine ended with ``return "retry"``
    instead of dying with CancelledError;

  * the harness side — a test that cancels the task and then does a plain
    ``await task`` (no timeout) hangs forever if any task cannot be torn
    down; the dead shutdown then leaks onto every following scenario.

Each scenario here runs in its OWN subprocess (so a hung scenario is killed
by the outer subprocess timeout and can never spill onto the next). Inside,
the task is cancelled and the exit is observed with the safe pattern:

    task.cancel()
    done, pending = await asyncio.wait([task], timeout=15)

If the task is still ``pending`` after 15 s the harness *abandons* it right
there (B5: the shutdown task cannot be torn down) and hard-exits, exactly as
a robust harness must — it never blocks on a plain ``await task``.

Scenarios:
  B1 idle     — cancelled while parked in run_until_disconnected() must end
                with CancelledError promptly (fix #2 regression guard).
  B2 startup  — cancelled while client.start() is still blocking: the
                CancelledError propagates as-is (it is not a login error,
                so it must NOT be converted into "retry"/"stop").
  B3 work     — cancelled while the first "online" note is mid-send:
                CancelledError propagates.
  B4 midrun   — cancelled seconds after startup (loops sleeping):
                CancelledError propagates promptly.
  B5 stuck    — a teardown that genuinely cannot finish (asyncio.sleep has
                been sabotaged to ignore cancellation): ``asyncio.wait``
                reports the task pending within 15 s, the harness abandons
                it and moves on instead of hanging forever.

Nothing sensitive is ever printed: the fake client uses dummy api_id/api_hash
and no real session file is opened (all client calls are stubbed).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(tempfile.gettempdir(), "jafj_shutdown_run")

# Inner harness: loads the real 95.py with a fake telethon, drives
# connect_and_run() and applies the SAFE cancel/observe pattern.
INNER = textwrap.dedent('''
    import asyncio, importlib.util, os, sys, threading, types
    import time as _time

    REPO = os.environ["REPO"]
    MODE = os.environ["MODE"]
    os.chdir(os.environ["DATA"])

    # ── fake telethon (no network, no real session file) ───────────
    CLIENTS = []
    BLOCK_START = asyncio.Event()     # never set: blocks client.start()
    BLOCK_SEND = asyncio.Event()      # never set: blocks the online note

    class FakeMsg:
        def __init__(self, mid, text=""):
            self.id = mid
            self.raw_text = text

    class FakeClient:
        def __init__(self, *a, **k):
            self.handlers = []
            self.sent = []
            self._id = 10000
            self._done = asyncio.Event()
            CLIENTS.append(self)
        def on(self, builder):
            def deco(fn):
                self.handlers.append((builder, fn))
                return fn
            return deco
        async def start(self, phone=None, **k):
            if MODE == "startup":
                print("READY startup", flush=True)
                await BLOCK_START.wait()   # cancelled while "logging in"
            return self
        async def get_me(self):
            return types.SimpleNamespace(first_name="Test", username="tester", id=555)
        async def send_message(self, peer, text=None, **k):
            self._id += 1
            msg = FakeMsg(self._id, text or "")
            self.sent.append(msg)
            if MODE == "work" and len(self.sent) == 1:
                print("READY work", flush=True)
                await BLOCK_SEND.wait()    # cancelled mid-note
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

    async def main():
        eng = mod.Engine()
        creds = {"api_id": 1, "api_hash": "h", "phone": "+989120000000"}

        if MODE == "stuck":
            # B5: run the whole bot inside a SEPARATE thread + its own event
            # loop. One of its background loops (the 60 s status loop) calls
            # eng.write_status() synchronously; that call parks the *worker
            # thread* inside a plain C-level time.sleep. A task.cancel()
            # only schedules a callback on the worker's loop — which can
            # never run while the thread is blocked — so the bot task cannot
            # be torn down. This is the exact model of an unkillable
            # teardown (a telethon RPC stuck in a blocking socket read). The
            # harness on the main thread stays healthy, polls the task state
            # thread-safely, sees it still pending after 15 s and abandons it.
            box = {}

            def _worker():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _run():
                    eng = mod.Engine()

                    def _never_returns(*a, **k):
                        while True:
                            _time.sleep(600)   # blocks THIS thread only
                    eng.write_status = _never_returns

                    box["loop"] = loop
                    box["task"] = asyncio.create_task(
                        mod.connect_and_run(eng, creds))
                    box["ready"] = True
                    # park the worker loop forever: the bot task keeps
                    # living in it until the thread is killed by os._exit.
                    while True:
                        await asyncio.sleep(3600)

                loop.run_until_complete(_run())

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            for _ in range(300):
                if box.get("ready"):
                    break
                await asyncio.sleep(0.05)
            task = box["task"]
            wloop = box["loop"]
            print("READY stuck handlers=2", flush=True)
            await asyncio.sleep(1.0)     # status loop parks inside the worker

            # ── safe harness: cancel, observe with a timeout ──
            wloop.call_soon_threadsafe(task.cancel)
            deadline = _time.monotonic() + 15
            pending = True
            while _time.monotonic() < deadline:
                if task.done():          # safe to read from any thread
                    pending = False
                    break
                await asyncio.sleep(0.2)

            if pending:
                print("ABANDONED pending=1", flush=True)
            else:
                exc = task.exception() if not task.cancelled() else \
                    asyncio.CancelledError()
                print("EXC CancelledError" if task.cancelled()
                      or isinstance(exc, asyncio.CancelledError)
                      else "RETURN done", flush=True)
            os._exit(0)                  # never block on a plain await; the
                                         # leaked worker must not spill over

        task = asyncio.create_task(mod.connect_and_run(eng, creds))

        # wait until the scenario reaches its blocking point / readiness
        for _ in range(400):
            if task.done():
                break
            if CLIENTS:
                c = CLIENTS[-1]
                if MODE == "work":
                    if c.sent:
                        break
                elif MODE == "startup":
                    # READY startup is printed from inside start(); once the
                    # client object exists it is about to (or already did)
                    # print — give it a moment and stop polling.
                    await asyncio.sleep(0.2)
                    break
                else:
                    if len(c.handlers) >= 2:
                        break
            await asyncio.sleep(0.05)

        if MODE in ("idle", "midrun", "stuck") and CLIENTS:
            print("READY %s handlers=%d"
                  % (MODE, len(CLIENTS[-1].handlers)), flush=True)
        if MODE == "midrun":
            await asyncio.sleep(2.0)     # let all loops settle into sleeping

        # ── the SAFE pattern: cancel, then observe with a timeout ──
        task.cancel()
        done, pending = await asyncio.wait([task], timeout=15)

        if pending:
            # A torn-down task that still refuses to finish: abandon it
            # here and move on — NEVER block on a plain `await task`.
            print("ABANDONED pending=%d" % len(pending), flush=True)
            os._exit(0)                  # hard exit: the leaked task must
                                         # never spill onto another scenario
        try:
            rc = await task
        except asyncio.CancelledError:
            print("EXC CancelledError", flush=True)
        else:
            print("RETURN %r" % (rc,), flush=True)

    asyncio.run(main())
''')


def run_scenario(mode, timeout=60):
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    env = dict(os.environ, REPO=REPO, DATA=ROOT, MODE=mode)
    try:
        p = subprocess.run([sys.executable, "-c", INNER], env=env,
                           text=True, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return None, out + "\n<<OUTER TIMEOUT: scenario hung and was killed>>"


CASES = [
    ("B1 idle", "idle",
     "cancel while parked in run_until_disconnected",
     "EXC CancelledError"),
    ("B2 startup", "startup",
     "cancel while client.start() blocks (not a login failure)",
     "EXC CancelledError"),
    ("B3 work", "work",
     "cancel while the online note is mid-send",
     "EXC CancelledError"),
    ("B4 midrun", "midrun",
     "cancel seconds after boot, loops sleeping",
     "EXC CancelledError"),
    ("B5 stuck", "stuck",
     "teardown cannot finish: harness abandons the pending task",
     "ABANDONED pending=1"),
]


def main():
    print("--- shutdown / cancellation scenarios (safe harness) ---")
    all_ok = True
    for title, mode, desc, want in CASES:
        rc, out = run_scenario(mode)
        keep = [l for l in out.splitlines()
                if l.startswith(("READY ", "EXC ", "RETURN ",
                                 "ABANDONED ", "<<OUTER"))]
        print("--- %s: %s ---" % (title, desc))
        print("\n".join(keep[-8:]) or out[-1200:])
        good = rc == 0 and want in out and "<<OUTER" not in out
        if mode == "stuck":
            good = good and "ABANDONED pending=1" in out
        else:
            good = good and "ABANDONED" not in out
        print("%s  %s\n" % ("PASS" if good else "FAIL", title))
        all_ok = all_ok and good
    print("ALL:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
