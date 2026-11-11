#!/usr/bin/env bash
# 一次性：把一台全新的 Ubuntu 服务器配置成 MagLens 阅读站，然后做第一次同步。
#   deploy/setup.sh ubuntu@服务器IP mag.example.com
#
# 做的事：装 python3-venv / rsync / caddy -> 建 maglens 用户和 /opt/maglens -> 上传代码、建虚拟环境
# -> 装 systemd 服务和 Caddy（自动 HTTPS）-> 让你设置管理员密码 -> 导出并上传全部已翻完的杂志 -> 启动。
# 可以重复运行：已经做过的步骤会跳过或覆盖成同样的结果。
#
# 前提：域名的 A 记录已经指向这台服务器（Caddy 申请证书要用）；防火墙放行 22/80/443；
#       ssh 用户能免密 sudo（腾讯云/阿里云 Ubuntu 镜像的 ubuntu 用户默认可以）。
set -euo pipefail
HOST=${1:?用法: deploy/setup.sh user@server 域名}
DOMAIN=${2:?用法: deploy/setup.sh user@server 域名}
ADMIN=${MAGLENS_ADMIN:-$(whoami)}
cd "$(dirname "$0")/.."
APP=/opt/maglens/app

ssh "$HOST" 'sudo -n true' 2>/dev/null || { echo "✗ $HOST 上的用户不能免密 sudo"; exit 1; }

echo "== 1/5 安装软件、建用户和目录"
ssh "$HOST" 'sudo bash -s' <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv rsync caddy >/dev/null
id maglens >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/maglens --shell /usr/sbin/nologin maglens
mkdir -p /opt/maglens/app /opt/maglens/data /opt/maglens/none
chown -R maglens:maglens /opt/maglens
REMOTE

echo "== 2/5 上传代码、建虚拟环境"
rsync -az --delete --rsync-path "sudo -u maglens rsync" \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude tests \
  ./ "$HOST:$APP/"
ssh "$HOST" "sudo -u maglens bash -c 'cd $APP && [ -x .venv/bin/python ] || python3 -m venv .venv; .venv/bin/pip install -q -r requirements.txt'"

echo "== 3/5 配置服务和 HTTPS（$DOMAIN）"
ssh "$HOST" "sudo bash -s" <<REMOTE
set -euo pipefail
cp $APP/deploy/maglens.service /etc/systemd/system/maglens.service
sed 's/mag\.example\.com/$DOMAIN/' $APP/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable maglens >/dev/null 2>&1
systemctl reload caddy || systemctl restart caddy
REMOTE

echo "== 4/5 设置管理员账号「$ADMIN」（想用别的名字：MAGLENS_ADMIN=名字 deploy/setup.sh ...）"
if ssh "$HOST" "sudo -u maglens $APP/.venv/bin/python -m maglens.users --data-dir /opt/maglens/data list" | grep -q "^$ADMIN"$'\t'; then
  echo "已存在，跳过（改密码：ssh -t $HOST sudo -u maglens $APP/.venv/bin/python -m maglens.users --data-dir /opt/maglens/data passwd $ADMIN）"
else
  ssh -t "$HOST" "cd $APP && sudo -u maglens .venv/bin/python -m maglens.users --data-dir /opt/maglens/data add '$ADMIN' --role admin"
fi

echo "== 5/5 导出并上传杂志、启动"
deploy/sync.sh "$HOST"

echo
echo "完成：https://$DOMAIN/"
echo "加朋友的账号：ssh -t $HOST 'cd $APP && sudo -u maglens .venv/bin/python -m maglens.users --data-dir /opt/maglens/data add 朋友名字'"
