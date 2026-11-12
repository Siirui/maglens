#!/usr/bin/env bash
# Run deploy/backup.sh every day at 21:00 on this Mac (launchd; if the Mac is asleep then, it runs once
# after waking). Log: ~/.maglens/server-backup/backup.log
#   deploy/backup-schedule.sh admin@<server>     # install or update, then run one backup right away
#   deploy/backup-schedule.sh --remove           # uninstall
set -euo pipefail
LABEL=net.maglens.backup
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi
HOST=${1:?usage: deploy/backup-schedule.sh user@server | --remove}
SCRIPT="$(cd "$(dirname "$0")" && pwd)/backup.sh"
LOGDIR="$HOME/.maglens/server-backup"
mkdir -p "$LOGDIR" "$(dirname "$PLIST")" && chmod 700 "$LOGDIR"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SCRIPT</string><string>$HOST</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOGDIR/backup.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/backup.log</string>
</dict>
</plist>
PLIST
plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart "$DOMAIN/$LABEL"
sleep 8
echo "installed $LABEL (daily 21:00). Last log lines:"
grep -v -E "WARNING|vulnerable|upgraded" "$LOGDIR/backup.log" | tail -2 || true
