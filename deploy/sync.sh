#!/usr/bin/env bash
# 本机 -> 服务器：导出已翻完的杂志，增量上传页面图 + 数据库 + 代码，然后重启服务。
#   deploy/sync.sh ubuntu@服务器IP              # 全部已翻完的杂志
#   deploy/sync.sh ubuntu@服务器IP 2026.12      # 只导出标题包含 2026.12 的（已上传的其他期不受影响）
#
# 要求：ssh 用户能免密 sudo（腾讯云/阿里云的 ubuntu 默认如此）。远端文件一律以 maglens 用户写入，
# 所以服务读写数据库不会有权限问题。auth.json（账号）、feedback.jsonl（读者反馈）和 access.jsonl(.1)（访问记录）只在服务器上维护，不会被覆盖。
set -euo pipefail
HOST=${1:?用法: deploy/sync.sh user@server [标题关键字...]}
shift
cd "$(dirname "$0")/.."
OUT=${MAGLENS_DIST:-$HOME/.maglens/dist}
APP=/opt/maglens/app
DATA=/opt/maglens/data

ssh "$HOST" 'sudo -n true' 2>/dev/null || { echo "✗ $HOST 上的用户不能免密 sudo"; exit 1; }

echo "== 导出"
if [ $# -gt 0 ]; then .venv/bin/python -m maglens.publish --out "$OUT" --only "$@"
else .venv/bin/python -m maglens.publish --out "$OUT"; fi

echo "== 上传代码"
rsync -az --delete --rsync-path "sudo -u maglens rsync" \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude tests --exclude '*.pdf' --exclude .DS_Store \
  ./ "$HOST:$APP/"
ssh "$HOST" "sudo -u maglens $APP/.venv/bin/pip install -q -r $APP/requirements.txt"

echo "== 上传页面图和数据库（增量）"
rsync -az --stats --rsync-path "sudo -u maglens rsync" --exclude auth.json --exclude feedback.jsonl --exclude 'access.jsonl*' "$OUT"/ "$HOST:$DATA/" \
  | grep -E "Number of files transferred|Total transferred file size" || true

echo "== 重启"
ssh "$HOST" 'sudo systemctl restart maglens && sleep 1 && systemctl is-active maglens'
echo "完成。"
