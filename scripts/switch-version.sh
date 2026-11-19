#!/usr/bin/env bash
# switch-version.sh <oxford|wiktionary> — switch the live Fusio variant.
#
# Both variants share ONE live DB (user data always identical); only the
# 4 content tables (entries / entry_examples / pronunciation_cache /
# vocabulary_books) and the served static dir differ.
#
# Flow: timestamped DB backup -> content switch (build_oxford_content.py,
# reversible, UPDATE-only, user tables untouched) -> flip the
# frontend-dist-current symlink -> systemctl restart vocab -> health check.
# Idempotent: re-running for the current variant is a no-op that still
# ends healthy.
#
# All assets referenced below live in persistent (non-/tmp) locations so the
# switch survives a server reboot. --wordnet-dir / --audio-dir are passed
# explicitly: without --audio-dir the oxford direction would write CDN
# hotlinks and the local /audio_oxford/ mirror would go unused.
set -euo pipefail

TARGET="${1:-}"
LIVE_DB=/home/ubuntu/vocabularylearning/backend/data/vocabulary.sqlite
STATIC_LINK=/home/ubuntu/vocabularylearning/frontend-dist-current
WIKT_DIR=/home/ubuntu/vocabularylearning/frontend-dist
OXF_DIR=/home/ubuntu/vocabularylearning-oxford/frontend-dist-oxford
SCRIPT=/home/ubuntu/oxford-data-inputs/build_oxford_content.py
WN_DIR=/home/ubuntu/oxford-data-inputs/wordnet
AUDIO_DIR=$OXF_DIR/audio_oxford
PY=/home/ubuntu/venv/bin/python3

usage() { echo "Usage: $0 <oxford|wiktionary>" >&2; exit 2; }
[ "$TARGET" = "oxford" ] || [ "$TARGET" = "wiktionary" ] || usage

CURRENT=$(readlink "$STATIC_LINK" 2>/dev/null || echo none)
echo "[switch] current static target: $CURRENT; requested: $TARGET"

echo "[backup] timestamped DB backup"
ts=$(date +%Y%m%d-%H%M%S)
cp "$LIVE_DB" "${LIVE_DB}.bak-${ts}"
echo "[backup] -> ${LIVE_DB}.bak-${ts}"

echo "[content] switching content tables to $TARGET variant"
$PY "$SCRIPT" --db "$LIVE_DB" --direction "$TARGET" \
    --wordnet-dir "$WN_DIR" --audio-dir "$AUDIO_DIR" --apply \
    --report "/tmp/switch-${TARGET}-${ts}-report.json"

echo "[static] flipping symlink"
if [ "$TARGET" = "oxford" ]; then NEW=$OXF_DIR; else NEW=$WIKT_DIR; fi
ln -sfn "$NEW" "$STATIC_LINK"

echo "[service] restart vocab"
sudo -n systemctl restart vocab
sleep 3
if curl -sf -o /dev/null http://127.0.0.1/api/health 2>/dev/null || curl -sf -o /dev/null http://127.0.0.1/ ; then
  echo "[health] OK — variant now: $TARGET"
else
  echo "[health] FAILED — check: journalctl -u vocab -n 50" >&2
  exit 1
fi
