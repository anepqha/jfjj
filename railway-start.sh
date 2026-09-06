#!/bin/sh
# ═══════════════════════════════════════════════════════════════════════════
#  Railway entrypoint for the JAFJ manager.
#
#  Two failure modes this script exists to prevent:
#
#  1) STALE CODE.  A Railway Volume mounted at /app hides everything the image
#     put in /app and keeps whatever the Volume already held. The container
#     then boots the OLD manager_82.py from the Volume on every redeploy, so a
#     fix never arrives and each self start still fails with
#     "به‌روزرسانی فایل سلف نشد: فایل 95.py پیدا نشد".
#     /opt/jafj is part of the image and cannot be shadowed by a Volume, so the
#     code is ALWAYS executed from there; the Volume only stores data.
#
#  2) CRASH LOOP.  Every manager exit used to exit the container, Railway
#     restarted it, and the same failure (Telegram login, FloodWait, leftover
#     lock file, empty Volume) repeated forever. The manager is supervised
#     here now: it is restarted in place, and only repeated instant crashes are
#     handed back to Railway's own restart policy.
# ═══════════════════════════════════════════════════════════════════════════
set -eu

IMAGE_DIR="${JAFJ_IMAGE_DIR:-/opt/jafj}"     # immutable, inside the image
APP_DIR="${JAFJ_APP_DIR:-/app}"              # usually where the Volume is
MANAGER_NAME="manager_82.py"
SELF_NAME="95.py"

log() { echo "SELFBOOT: $*"; }

pick_python() {
    if [ -n "${PYTHON:-}" ] && command -v "$PYTHON" >/dev/null 2>&1; then
        echo "$PYTHON"
        return
    fi
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1; then
            echo "$c"
            return
        fi
    done
    echo python3
}
PY="$(pick_python)"

# ── 1) where does the data live? (Volume wins, image dir is a last resort) ──
if [ -z "${DATA_DIR:-}" ]; then
    if [ -d "$APP_DIR" ] && [ -w "$APP_DIR" ]; then
        DATA_DIR="$APP_DIR"
    else
        DATA_DIR="/data"
        mkdir -p "$DATA_DIR" 2>/dev/null || DATA_DIR="$IMAGE_DIR"
    fi
fi
# manager_82.py resolves DATA_DIR against cwd; make it absolute so a relative
# value keeps meaning the same place it did when the code ran from /app.
mkdir -p "$DATA_DIR" 2>/dev/null || true
_abs="$(cd "$DATA_DIR" 2>/dev/null && pwd)" && DATA_DIR="$_abs"
export DATA_DIR

# ── 2) the code always comes from the image ──────────────────────────────────
MANAGER="$IMAGE_DIR/$MANAGER_NAME"
SELF="$IMAGE_DIR/$SELF_NAME"
export JAFJ_SELFBOT_REF="${JAFJ_SELFBOT_REF:-$SELF}"

if [ ! -f "$MANAGER" ]; then
    # Old image without /opt/jafj: fall back to a surviving copy instead of
    # dying in a restart loop.
    if [ -f "$DATA_DIR/$MANAGER_NAME" ]; then
        MANAGER="$DATA_DIR/$MANAGER_NAME"
        log "WARNING: $IMAGE_DIR/$MANAGER_NAME missing — running the copy in $DATA_DIR (rebuild the image)"
    else
        echo "ERROR: no $MANAGER_NAME in $IMAGE_DIR or $DATA_DIR — image is broken or too old, redeploy." >&2
        exit 1
    fi
fi
if [ ! -s "$SELF" ]; then
    log "WARNING: $SELF is missing from the image — self bots cannot start until the image is rebuilt"
fi
# A stale copy in the Volume is not used; say so, because that is exactly what
# made previous redeploys look like they did nothing.
if [ "$DATA_DIR" != "$IMAGE_DIR" ] && [ -f "$DATA_DIR/$MANAGER_NAME" ] \
        && ! cmp -s "$MANAGER" "$DATA_DIR/$MANAGER_NAME" 2>/dev/null; then
    log "note: $DATA_DIR/$MANAGER_NAME differs from the image copy and is NOT executed"
fi

log "code=$IMAGE_DIR data=$DATA_DIR python=$PY self=$([ -s "$SELF" ] && echo ok || echo MISSING)"

# ── 3) run it ───────────────────────────────────────────────────────────────
if [ "${JAFJ_SUPERVISE:-1}" = "0" ]; then
    cd "$DATA_DIR" 2>/dev/null || true
    exec "$PY" "$MANAGER"
fi

CHILD=0
STOPPING=0
forward() {
    STOPPING=1
    if [ "$CHILD" -gt 0 ]; then
        kill -TERM "$CHILD" 2>/dev/null || true
    fi
    return 0
}
trap forward TERM INT

crashes=0
MAX_CRASHES="${JAFJ_MAX_CRASHES:-5}"      # پشت‌سرهم → تحویل به Railway
NAP_BASE="${JAFJ_NAP_BASE:-5}"            # ثانیه، هر کرش ۵ تا بیشتر
CRASH_WINDOW="${JAFJ_CRASH_WINDOW:-20}"   # کمتر از این = کرش سریع
while :; do
    cd "$DATA_DIR" 2>/dev/null || true
    "$PY" "$MANAGER" &
    CHILD=$!
    started=$(date +%s)
    set +e
    wait "$CHILD"
    code=$?
    # a trapped signal interrupts wait; keep waiting while the child lives
    while [ "$code" -gt 128 ] && kill -0 "$CHILD" 2>/dev/null; do
        wait "$CHILD"
        code=$?
    done
    set -e
    CHILD=0

    if [ "$STOPPING" = 1 ]; then
        exit "$code"
    fi
    if [ "$code" = 0 ]; then
        log "manager exited cleanly"
        exit 0
    fi

    ran=$(( $(date +%s) - started ))
    if [ "$ran" -lt "$CRASH_WINDOW" ]; then
        crashes=$((crashes + 1))
    else
        crashes=0
    fi
    if [ "$crashes" -ge "$MAX_CRASHES" ]; then
        log "manager crashed $crashes times in a row — giving up so Railway reports it"
        exit "$code"
    fi
    nap=$(( NAP_BASE * (crashes + 1) ))
    if [ "$nap" -gt 60 ]; then
        nap=60
    fi
    log "manager exited with code $code after ${ran}s — restarting in ${nap}s"
    if [ "$nap" -gt 0 ]; then
        sleep "$nap"
    fi
done
