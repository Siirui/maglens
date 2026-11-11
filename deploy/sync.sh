#!/usr/bin/env bash
# 本地：把翻译完成的杂志导出并同步到服务器。
#   deploy/sync.sh user@your-server            # 全部已完成的杂志
#   deploy/sync.sh user@your-server 2026.11    # 只同步标题包含 2026.11 的
set -euo pipefail
HOST=${1:?用法: deploy/sync.sh user@server [标题关键字...]}
shift || true
cd "$(dirname "$0")/.."
OUT=${MAGLENS_DIST:-$HOME/.maglens/dist}

.venv/bin/python -m maglens.publish --out "$OUT" ${1:+--only "$@"}
# auth.json 只在服务器上维护（python -m maglens.users），不覆盖；页面图增量上传
rsync -az --info=progress2 --exclude auth.json "$OUT"/ "$HOST":/opt/maglens/data/
ssh "$HOST" 'sudo systemctl restart maglens && systemctl is-active maglens'
echo "完成。"
