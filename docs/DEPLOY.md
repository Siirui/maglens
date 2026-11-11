# 部署到云服务器（自用 / 朋友圈）

思路：**翻译在自己电脑上跑，服务器只负责给人看。** 服务器上没有 PDF，也不调用任何模型。
它只保存预渲染好的页面图（WebP）和一份精简数据库（不含本地路径和花费），并且必须登录才能访问。

```
本机：cli_runner 翻译 ──> maglens.publish 导出 dist/ ──rsync──> 服务器 /opt/maglens/data
服务器：Caddy (HTTPS) ──> uvicorn (run.py --manual) ──> 读 data/
```

## 0. 准备
- **服务器**：腾讯云或阿里云的**香港**轻量应用服务器，2 核 2G 或 4G，系统盘 40G 以上，Ubuntu 22.04/24.04，约 ¥30–60/月。放在香港不需要 ICP 备案。
- **域名**：任意注册商都可以，约 ¥50/年。添加一条 A 记录指向服务器 IP，例如 `mag.example.com`。
- **防火墙**：只放行 22、80、443。

## 1. 服务器初始化（一次性）
```bash
sudo apt update && sudo apt install -y python3-venv rsync caddy
sudo useradd -r -m -d /opt/maglens maglens
sudo -u maglens git clone <你的仓库> /opt/maglens/app      # 或者用 rsync 把代码传上去
cd /opt/maglens/app
sudo -u maglens python3 -m venv .venv
sudo -u maglens .venv/bin/pip install -r requirements.txt
sudo mkdir -p /opt/maglens/data /opt/maglens/none && sudo chown -R maglens /opt/maglens
# 让本机的 ssh 用户也能往 data 写（用于 rsync）：
sudo chgrp -R "$USER" /opt/maglens/data && sudo chmod -R g+rwX /opt/maglens/data

sudo cp deploy/maglens.service /etc/systemd/system/
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile      # 先把里面的 mag.example.com 改成你的域名
sudo systemctl daemon-reload && sudo systemctl enable --now maglens && sudo systemctl reload caddy
```

## 2. 建账号（在服务器上）
```bash
cd /opt/maglens/app
sudo -u maglens .venv/bin/python -m maglens.users --data-dir /opt/maglens/data add 你的名字 --role admin
sudo -u maglens .venv/bin/python -m maglens.users --data-dir /opt/maglens/data add 朋友A           # 默认是读者
sudo -u maglens .venv/bin/python -m maglens.users --data-dir /opt/maglens/data list
```
- 读者：只能看已经翻完的杂志；没有「重译」「框选识别」「术语表」「导出」，也看不到花费和进度。
- 管理员：功能和本地一样。服务器上没有模型，所以「重译」「框选识别」在服务器上不起作用。
- 账号保存在 `data/auth.json`，同步时不会被覆盖。改密码用 `passwd`，删账号用 `remove`。

## 3. 同步内容（在自己电脑上）
```bash
deploy/sync.sh ubuntu@你的服务器IP               # 导出全部已完成的杂志，增量上传，重启服务
deploy/sync.sh ubuntu@你的服务器IP 2026.12       # 只同步新的一期
```
- 一本约 177 页，三档清晰度的 WebP 合计约 100–150MB；首次同步全部 12 本约 1.5GB。
- 还没翻完的杂志不会被导出。读者端会显示为「即将上线」；如果想直接隐藏，把 `config.toml` 里的 `[reader] show_incomplete` 改成 `false`。

## 4. 日常
- 查看日志：`journalctl -u maglens -f`
- 更新代码：`cd /opt/maglens/app && git pull && sudo systemctl restart maglens`
- 备份：服务器上只有 `auth.json` 是独有的，其余内容都能从本机重新导出。

## 成本（每月）
| 项目 | 费用 |
|---|---|
| 香港轻量服务器（自带每月 1TB 左右流量） | ¥30–60 |
| 域名（按年摊） | ≈ ¥4 |
| 翻译（本机用 Claude 订阅额度，按标价折算） | 每期约 $2–4 |

读一整本大约消耗 50–80MB 流量，几十个人随便看也用不完流量包。

## 注意
这套部署只适合**自己和少数朋友私下使用**：不开放注册、禁止搜索引擎收录、页面图也需要登录才能访问。
不要公开传播，也不要拿杂志内容收费。杂志和译文的版权属于出版方。
