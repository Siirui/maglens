# MagLens — 给 AI 助手的项目说明

日文杂志（目前是 POPEYE）阅读器：左边看原页，右边看中文译文。翻译在本机离线批量跑，服务器只读。
线上：https://maglens.net 。完整背景、历史和已知问题见 `docs/DEVLOG.md`，部署细节见 `docs/DEPLOY.md`。**开始一个新任务前先读 DEVLOG 的「当前状态」「已知问题」和 `docs/ROADMAP.md`（优先级和分工）。**

## 不能违反的约束
1. **翻译只走 `claude -p`**（`python -m maglens.cli_runner`）。用户没有 Console API key，不要引入 Anthropic SDK 直连，也不要把订阅 OAuth token 当 API key 用。
2. **服务器永不调用模型。** 线上跑 `run.py --manual`；任何会花钱的接口必须加进 `server.py` 的 `ADMIN_ONLY`，并且 `page_data` 只有 admin 才会触发 `jobs.ensure`。
3. **服务器上不放 PDF、本机路径、花费。** 只同步 `maglens.publish` 导出的 WebP + 精简数据库。`sync.sh`/`setup.sh` 都排除了 `*.pdf`，改动时保持这一点。
4. **默认需要登录，禁止搜索引擎收录**（`X-Robots-Tag`、`robots.txt`、`noindex`）。杂志和译文的版权属于出版方：新功能面向私下阅读，任何公开访问（游客页、分享链接、推广）都要先和用户确认范围。
5. **读者看不到管理信息**：花费、进度、模型名、页面类型标签、只有编号的块都要对 reader 隐藏（前端 `.admin-only` + 后端不返回）。

## 目录
```
maglens/            后端（FastAPI，Python ≥ 3.11）
  server.py         所有 HTTP 接口 + 登录中间件 + 角色权限（ADMIN_ONLY）
  auth.py           scrypt 密码、HMAC 签名会话 cookie；账号在 <data_dir>/auth.json
  users.py          账号命令行（add / passwd / rm / list）
  cli_runner.py     批量翻译（claude -p），断点续跑
  extract.py        识别 + 翻译引擎（Claude / Mock / Manual）
  prompts.py        提示词；改动要升 PROMPT_VERSION
  store.py          SQLite（magazines / pages / glossary）
  library.py        扫描杂志目录，按文件内容识别同一本杂志
  render.py         PDF → PNG
  publish.py        导出部署包 dist/（WebP 三档 360/1600/2400 + 精简库）
web/                前端，无构建步骤：index.html + app.js（单文件，原生 JS）+ style.css；login.html
deploy/             Caddyfile、systemd 服务、setup.sh（仅 Ubuntu）、sync.sh
tests/              test_access.py（权限）、test_pipeline.py（流程）；fake_claude / fake_client 回放响应
docs/               DEVLOG.md（开发记录）、DEPLOY.md、LOCAL_RUN.md
```
数据不在仓库里：杂志 PDF 在 `~/Magazines/2026`，数据库和缓存在 `~/.maglens`，部署包在 `~/.maglens/dist`。

## 常用命令
```bash
source .venv/bin/activate
python -m pytest -q tests                  # 不需要 key，必须全过
python -m pyflakes maglens tests run.py    # 必须无警告
python run.py --manual                     # 本机只读预览 → http://127.0.0.1:8765/
python run.py --mock --dir <测试目录>       # 只用 PDF 文字层调界面
python tests/make_sample.py x.pdf          # 生成 3 页测试杂志
python -m maglens.publish --out ~/.maglens/dist
deploy/sync.sh admin@<服务器IP>        # 同步代码和数据到线上 （真实地址见 docs/PRIVATE.md）
```

## 写代码的规矩
- **改接口 = 改测试。** 新增或修改接口时，在 `tests/test_access.py` 里写清楚 reader / admin / 未登录各自的结果。权限默认拒绝。
- **UI 文案用简体中文**，口吻简短、直接。代码注释和提交信息用英文。
- **设计取向**：大道至简，能藏就藏，少加选项。书架、登录页、品牌用街头风（黑粗边框、硬投影、黄 `--yellow` / 红 `--red` / 蓝 `--blue`、贴纸、片假名マグレンズ）；**阅读页本身保持安静**，风格只点缀在按钮、页码、标题这类地方。颜色一律用 `style.css` 里 `:root` 的变量，深色模式要一起改。Logo（图标 A + Dela Gothic 字标）已定稿，不要改。
- **字体本地托管**：读者在大陆，不能依赖 Google Fonts 或任何境外 CDN。前端不引第三方脚本。
- **手机是一等公民**：改阅读页要在 375px 宽（iPhone）和 360px 宽（安卓）下检查，注意 `100dvh`、`env(safe-area-inset-*)`、微信内置浏览器。
- 不要引入前端构建工具或框架，除非用户同意。
- 改提示词要升 `PROMPT_VERSION`；已经翻好的数据不会自动重翻。

## 部署和数据安全
- `maglens.publish` **不能并发运行**（临时文件名固定），两个会话不要同时导出或同时 sync。
- 服务器上的 `auth.json`（账号）是唯一不能从本机重建的东西，改账号逻辑前提醒用户备份。
- 推送到线上、改服务器配置、删除数据都属于对外/不可逆操作，**先问用户**。
- 线上机器是 Alibaba Cloud Linux 4（dnf），不是 Ubuntu；登录用户 `admin`，服务用户 `maglens`，以 maglens 身份执行命令要用 `sudo -u maglens env -C /opt/maglens/app …`。

## 多个会话并行时
- 每个会话用独立的 git worktree 和分支（`feat/<主题>`），做完开 PR 或让用户合并。
- 按文件分工，避免两个会话同时改同一个文件：
  - 后端权限/账号：`maglens/server.py`、`maglens/auth.py`、`maglens/users.py`、`web/login.html`、`tests/test_access.py`
  - 阅读页前端：`web/app.js`、`web/style.css`、`web/index.html`（同一时间只给一个会话）
  - 部署/运维：`deploy/`、`docs/DEPLOY.md`
- 只有一个会话可以跑 `publish` / `sync.sh`。

## 做完一件事之后
1. 测试 + pyflakes 全过。
2. 在 `docs/DEVLOG.md` 更新「当前状态」「这次做了什么」「已知问题」「下一步」（中文，写清楚原因）。
3. 在功能分支上提交，跑一次 code review；没有要改的问题就直接合并进 main，接着做 ROADMAP 的下一项，不用再问用户。review 发现问题就先修好，再 review。
4. 合并后可以用 deploy/sync.sh 做常规发布；改服务器配置、SSH、防火墙、删除线上数据仍然要先问用户。
5. 提交信息用英文祈使句，一件事一个提交。
