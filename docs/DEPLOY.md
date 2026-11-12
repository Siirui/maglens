# 部署到云服务器（自用 / 朋友圈）

思路：**翻译在自己电脑上跑，服务器只负责给人看。** 服务器上没有 PDF，也不调用任何模型。
它只保存预渲染好的页面图（WebP）和一份精简数据库（不含本地路径和花费），并且必须登录才能访问。

```
本机：cli_runner 翻译 ──> maglens.publish 导出 dist/ ──rsync──> 服务器 /opt/maglens/data
服务器：Caddy (HTTPS) ──> uvicorn (run.py --manual) ──> 读 data/
```

## 设计决策（2026-11-11 定）
- **放境外（香港），不做 ICP 备案。** 读者在大陆，香港延迟最低；服务器不调模型，所以不受「Anthropic API 不支持香港」的限制。
- **所有涉及模型的功能只在本机用，永不开放给读者。** 有三层保证：服务器上没有 API key 且跑 `--manual`（`ManualEngine` 什么都不做）；后端 `ADMIN_ONLY` 拦截重译、框选识别、术语表、导出，读者直接请求会返回 403；`page_data` 只有 admin 才会触发翻译。
- **同步 WebP，不同步 PDF。** 实测 `popeye2026.11`（177 页）：PDF 257MB，三档 WebP 约 98MB。读者只需要图和译文，PDF 文字层只在本机翻译时用。完整原版 PDF 也不放在面向读者的机器上。
- **读者端交互全部保留**：悬浮联动框选（基于同步过去的 `bbox`）、中文/对照/原文切换、跨页、缩放、翻页。本机框选补漏的 `supplement` 块会随下次同步上线。
- 暂不上 CDN/R2：页面图需要登录才能访问，公共 CDN 会绕过鉴权；按估算的流量，一台香港轻量服务器足够。

## 现在的线上环境（2026-11-12 上线）
| 项目 | 情况 |
|---|---|
| 网址 | https://maglens.net（Cloudflare 注册，A 记录「仅 DNS」，灰色云朵） |
| 服务器 | 阿里云轻量应用服务器**吉隆坡**，**通用型**（不是国际型），2 vCPU / 1 GiB / 30 GiB，200Mbps 峰值，$5/月；加了 2G swap |
| 系统 | **Alibaba Cloud Linux 4**（dnf），不是 Ubuntu；登录用户 `admin`（免密 sudo） |
| HTTPS | Caddy v2.11.9，GitHub 官方二进制（校验过 SHA-512）+ 手写的 `/etc/systemd/system/caddy.service`；Let's Encrypt 证书自动续期 |
| 同步 | `deploy/sync.sh admin@<服务器IP>` |

**`deploy/setup.sh` 只支持 Ubuntu**（第一步用 `apt-get`），这台机器是照着它的步骤手动部署的。如果以后要在 Alibaba Cloud Linux / CentOS 上重装，第 1 步改成：`dnf install -y rsync tar`，再从 [Caddy 的 GitHub 发布页](https://github.com/caddyserver/caddy/releases) 下载二进制装到 `/usr/bin/caddy`，并建好 `caddy` 用户和 systemd 服务。其余步骤相同。

## 0. 准备
- **服务器**：轻量应用服务器，2 核、1G 以上内存（1G 时加 2G swap），系统盘 30G 以上，约 ¥30–60/月。放在境外不需要 ICP 备案。读者在大陆，所以**一定选通用型，不要选国际型**（国际型的线路没有针对大陆优化），机房以大陆晚高峰实测为准。
- **域名**：推荐 Cloudflare Registrar / Porkbun / Namecheap（按成本价，不需要国内实名），`.com` 约 $10/年。添加一条 A 记录指向服务器 IP，例如 `mag.example.com`。如果 DNS 托管在 Cloudflare，**代理开关要关掉（灰色云朵，仅 DNS）**：橙色代理会让大陆访问绕路变慢。
- **防火墙**：只放行 22、80、443。

## 1. 一键初始化（在自己电脑上，只需一次）
确认 DNS 的 A 记录已经生效（`ping mag.example.com` 能看到服务器 IP），然后：
```bash
deploy/setup.sh ubuntu@你的服务器IP mag.example.com
```
它会依次完成：安装 python3-venv、rsync、caddy；创建 `maglens` 用户和 `/opt/maglens`；上传代码、建好虚拟环境；
装好 systemd 服务和 Caddy（自动申请 HTTPS 证书）；**让你设置管理员密码**（默认用户名就是你电脑的用户名，想换就用 `MAGLENS_ADMIN=名字 deploy/setup.sh ...`）；
最后导出全部已翻完的杂志、上传并启动服务。中途失败了可以直接重跑，做过的步骤会跳过。

前提是 ssh 用户能免密 sudo，腾讯云和阿里云的 Ubuntu 镜像默认就可以，脚本开头会先检查。
服务器上不需要 git，代码由本机直接上传。

> 保险：服务器还没有任何账号时，所有经过 Caddy 进来的请求都会被拒绝（403），不会把管理员权限漏给外人。

## 2. 加朋友的账号（网页上）
用管理员账号登录网站，点右上角用户名旁边的 **「账号」**：
- **新建**：填用户名，密码可以自己填，也可以点「随机」生成；选择读者或管理员，点「创建」。密码只显示这一次，点「复制账号和密码」，会把网址、用户名和密码一起复制，直接发给朋友。
- **重置密码**：生成一个新的随机密码，对方在所有设备上都会被退出登录。
- **改角色 / 删除**：删除要点两次确认。不能删除自己，也不能取消自己的管理员身份，所以至少总有一个管理员。

命令行仍然可用（比如忘了管理员密码时）：
```bash
ssh -t admin@服务器IP 'sudo -u maglens env -C /opt/maglens/app .venv/bin/python -m maglens.users --data-dir /opt/maglens/data passwd 名字'
```
`/opt/maglens` 只有 maglens 用户能进入，所以要用 `env -C` 切换目录，不能先 `cd` 再 `sudo`。
- 读者：只能看已经翻完的杂志；没有「重译」「框选识别」「术语表」「导出」，也看不到花费和进度。
- 管理员：服务器上没有模型，所以「重译」「框选识别」在线上会自动隐藏。
- 账号保存在 `data/auth.json`，同步时不会被覆盖。改密码用 `passwd`，删账号用 `remove`，列出账号用 `list`。
- 登录有防暴力破解：同一个 IP 15 分钟内输错 10 次会被暂时锁住。
- 防整本抓取：读者（管理员不受限）按「账号 + IP」限速——大图和译文各每分钟 120 次、每小时 600 次，缩略图每 10 分钟 400 次；同一账号所有 IP 加起来，大图和译文各每小时最多 3000 次（防止公开试读账号被很多 IP 一起刷）。IPv6 按 /64 网段算一个来源。超过返回 429「看得太快了，休息一下再继续」，带 `Retry-After`。正常翻页（包括快速翻阅和预加载）碰不到；数字和理由在 `maglens/guard.py` 顶部。计数在内存里，重启服务后清零。

## 3. 同步内容（在自己电脑上，以后每次新翻了杂志或改了代码）
```bash
deploy/sync.sh ubuntu@你的服务器IP               # 导出全部已翻完的杂志，增量上传图、数据库和代码，重启服务
deploy/sync.sh ubuntu@你的服务器IP 2026.12       # 只导出新的一期（已上传的其他期不受影响）
```
- 一本约 177 页，三档清晰度的 WebP 合计约 80–100MB；首次同步全部 12 本约 1GB。
- 还没翻完的杂志不会被导出。读者端会显示为「即将上线」；如果想直接隐藏，把 `config.toml` 里的 `[reader] show_incomplete` 改成 `false`。

## 4. 日常
- 查看日志：`journalctl -u maglens -f`
- 更新代码：本机直接 `deploy/sync.sh ubuntu@服务器IP`，代码会随数据一起上传并重启服务
- 查看读者反馈：反馈存在服务器的 `data/feedback.jsonl`（每行一条 JSON），和 `auth.json` 一样只在服务器上，`sync.sh` 不会上传或覆盖它。管理员也可以用 `GET /api/feedback` 查看。拿到本机：
  ```bash
  ssh admin@<服务器IP> 'sudo cat /opt/maglens/data/feedback.jsonl' > feedback.jsonl
  ```
- 查看访问记录：读者每看一页译文（`GET /api/mag/{id}/page/{n}`，不含图片，不记管理员）和被限速（被拒后还在连续请求的，同一类别每 10 秒只记一行，免得刷满日志），都会在服务器的 `data/access.jsonl` 追加一行 JSON：`{"time": UTC 时间, "user", "ip", "mag_id", "page", "event": "view" 或 "limited:<类别>"}`，类别是 `img` / `thumb` / `data` / `img_account` / `data_account`。超过约 20MB 时改名为 `access.jsonl.1`（只留一个旧文件）。和 `feedback.jsonl` 一样只在服务器上，`sync.sh` 不会上传或覆盖。管理员可以用 `GET /api/access/summary?hours=24`（最多 168）看每个账号的浏览数、不同页数、IP 数、最后访问时间、被限速次数，以及最近 20 次限速。拿到本机：
  ```bash
  ssh admin@<服务器IP> 'sudo cat /opt/maglens/data/access.jsonl' > access.jsonl
  ```
- 备份：服务器上只有 `auth.json`、`feedback.jsonl` 和 `access.jsonl` 是独有的，其余内容都能从本机重新导出。**真正要备份的是本机**：杂志 PDF 和 `~/.maglens/maglens.sqlite3`（花钱翻出来的结果）。放移动硬盘或私有云盘做冷备份，不要放在读者用的服务器上。

## 成本（每月）
| 项目 | 费用 |
|---|---|
| 轻量服务器（现在是阿里云吉隆坡 $5/月） | ¥30–60 |
| 域名（按年摊） | ≈ ¥4 |
| 翻译（本机用 Claude 订阅额度，按标价折算） | 每期约 $2–4 |

读一整本大约消耗 50–80MB 流量，几十个人随便看也用不完流量包。

## 注意
这套部署只适合**自己和少数朋友私下使用**：不开放注册、禁止搜索引擎收录、页面图也需要登录才能访问。
不要公开传播，也不要拿杂志内容收费。杂志和译文的版权属于出版方。

## 待办
- [x] **前端预加载后续页面图片（优先做）**：现在 `web/app.js` 只在翻到某一页时才请求这一页的图，线路一般时每次翻页都要等。改成看当前页时，后台用 `new Image()` 预取后面 1–2 页（单页模式按页、跨页模式按跨页；翻页方向跟 `S.rtl` 走），宽度和当前选的 1600/2400 档一致，这样能命中浏览器缓存。不涉及模型，读者端也生效。服务器已经返回 `Cache-Control: max-age=86400`，不用改后端。
- [x] 选机房：阿里云吉隆坡通用型（itdog 实测可以）。还差一步：**上线后请国内的朋友在晚上 8–11 点翻十几页**，确认晚高峰不卡。如果卡，备选是腾讯云轻量香港（2 核 2G / 40GB / 20Mbps / 0.5TB，38 元/月）。
- [x] 网页端账号管理（管理员点右上角「账号」）。
- [ ] `deploy/setup.sh` 支持 dnf 系统（Alibaba Cloud Linux / CentOS），或者在开头检测到不是 apt 时直接报错说明。
- [ ] `maglens.publish` 不能两个进程同时跑：临时文件名固定是 `xxx.tmp`，同时渲染同一页时会互相覆盖并报错（2026-11-11 本机两个 session 同时导出时出现过）。改成带进程号的临时文件名，或者加一个导出锁。

### 可选
- [x] 只读模式（`--manual`）下隐藏「框选识别」「重译」按钮和快捷键 R，免得用管理员登录线上时点了没反应。
- [ ] 决定是否给读者开放「显示框（B）」：它不用模型，去掉 `index.html` 里按钮的 `admin-only`，再去掉 `app.js` 里 B 快捷键的 `isAdmin()` 判断即可。
