#!/usr/bin/env bash
# 服务器 -> 本机：备份只存在于服务器上的文件（账号 auth.json、读者反馈、访问日志）。
#   deploy/backup.sh admin@服务器IP              # 存到 ~/.maglens/server-backup/，保留最近 30 份
# auth.json 里有密码哈希和会话签名密钥：备份目录权限是 700，不要放进 iCloud 或网盘同步目录。
set -euo pipefail
HOST=${1:?用法: deploy/backup.sh user@server}
DEST=${MAGLENS_BACKUP_DIR:-$HOME/.maglens/server-backup}
KEEP=${MAGLENS_BACKUP_KEEP:-30}
mkdir -p "$DEST" && chmod 700 "$DEST"
OUT="$DEST/maglens-$(date +%Y%m%d-%H%M%S).tgz"
umask 077
ssh "$HOST" 'sudo sh -c "cd /opt/maglens/data && tar -czf - \$(ls auth.json feedback.jsonl access.jsonl access.jsonl.1 2>/dev/null)"' > "$OUT.part"
tar -tzf "$OUT.part" | grep -qx auth.json || { rm -f "$OUT.part"; echo "✗ 备份里没有 auth.json，放弃"; exit 1; }
mv "$OUT.part" "$OUT"
echo "已备份 → ${OUT}（$(tar -tzf "$OUT" | tr '\n' ' ')）"
ls -1t "$DEST"/maglens-*.tgz | tail -n +$((KEEP + 1)) | xargs -r rm -f
