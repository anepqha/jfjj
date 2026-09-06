#!/bin/sh
# Restore the selfbot source before starting the existing manager.
# A Railway Volume mounted at /app can hide or retain a broken old 95.py,
# while /opt/jafj is part of the freshly built image and remains untouched.
set -eu

APP_DIR="${JAFJ_APP_DIR:-/app}"
SELF_SOURCE="${JAFJ_IMAGE_SELFBOT:-/opt/jafj/95.py}"
SELF_TARGET="$APP_DIR/95.py"
MANAGER="$APP_DIR/manager_82.py"

if [ ! -s "$SELF_SOURCE" ]; then
    echo "ERROR: clean selfbot source is missing: $SELF_SOURCE" >&2
    exit 1
fi

if [ ! -f "$MANAGER" ]; then
    echo "ERROR: manager is missing: $MANAGER" >&2
    exit 1
fi

# Copy to a temporary name then rename it. rename(2) replaces a stale or
# circular symlink instead of following it, so the next manager boot sees a
# normal 95.py even when a legacy /app Volume contains a broken link.
TMP_TARGET="${SELF_TARGET}.incoming.$$"
rm -f "$TMP_TARGET"
cp "$SELF_SOURCE" "$TMP_TARGET"
mv -f "$TMP_TARGET" "$SELF_TARGET"

echo "SELFBOOT: restored $SELF_TARGET from immutable $SELF_SOURCE"
exec python "$MANAGER"
