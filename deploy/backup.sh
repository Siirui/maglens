#!/usr/bin/env bash
# Server -> this Mac: back up the files that exist only on the server (accounts, reader feedback, access log).
#   deploy/backup.sh admin@<server>      # into ~/.maglens/server-backup/, keeps the newest 30
# auth.json holds password hashes and the session signing key: the backup dir is 700; keep it out of
# iCloud and other synced folders.
set -euo pipefail
HOST=${1:?usage: deploy/backup.sh user@server}
DEST=${MAGLENS_BACKUP_DIR:-$HOME/.maglens/server-backup}
KEEP=${MAGLENS_BACKUP_KEEP:-30}
mkdir -p "$DEST" && chmod 700 "$DEST"
OUT="$DEST/maglens-$(date +%Y%m%d-%H%M%S).tgz"
umask 077
trap 'rm -f "${OUT}.part"' EXIT          # never leave a half-written copy of auth.json behind
ssh "$HOST" 'sudo -n true' 2>/dev/null || { echo "✗ ${HOST}: sudo needs a password"; exit 1; }
ssh "$HOST" 'sudo -n sh -c "cd /opt/maglens/data && tar -czf - \$(ls auth.json feedback.jsonl access.jsonl access.jsonl.1 2>/dev/null)"' > "${OUT}.part"
tar -tzf "${OUT}.part" | grep -qx auth.json || { echo "✗ auth.json missing from the backup, giving up"; exit 1; }
mv "${OUT}.part" "$OUT"
echo "backed up -> ${OUT} ($(tar -tzf "$OUT" | tr '\n' ' '))"
ls -1t "$DEST"/maglens-*.tgz | tail -n +$((KEEP + 1)) | xargs -r rm -f
