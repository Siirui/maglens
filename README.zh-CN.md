[English](README.md) · **简体中文**

# MagLens

日文杂志阅读器，每一页旁边都有中文译文。用 Claude 视觉模型识别整页，再拿 PDF 文字层交叉核对、补上漏掉的部分，翻译成大陆生活方式杂志的中文风格。屏幕上的主角始终是杂志本身，译文在需要的时候出现。

## 功能

- **原文和译文对照**：在页面上指向或点一段文字，右边对应的译文就会高亮，反过来也一样。可以切换中文、对照、原文三种显示。
- **像看杂志一样翻**：默认按中文书的习惯翻页（下一页在右边，也可以切成日式），跨页保持杂志原本的右装订、跨页、多种适应方式、缩放、左右滑动翻页，后面几页会提前加载。
- **手机**：竖屏时杂志页铺满屏幕，译文放在底部抽屉里，有三档高度；也可以打开「点读」，点哪段就在旁边弹出那段的译文。
- **整本批量翻译，可以断点续跑**：在后台翻完一整本；中途中断，重跑会从停下的地方继续，已经翻好的页不会再花钱。
- **每个系列一份术语表**：店名、品牌、人名在各期之间保持一致。
- **小范围私下部署**：读者和管理员两种账号，可以限定某个账号只能看哪几期，读者可以在页面里直接提交反馈。服务器上只有预渲染的页面图和翻好的译文，没有 PDF，也不调用任何模型。

## 在 Mac 上运行（Python ≥ 3.11）

```bash
git clone https://github.com/Siirui/maglens.git && cd maglens
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

在 `config.toml` 的 `[library] dir` 里设置杂志 PDF 所在的目录，也可以用 `--dir` 临时指定。目录里如果有整本扫描成 JPG 的文件夹，会自动转成 PDF，放在 `~/.maglens/converted/`，原文件夹不会改动。

译文保存在 `~/.maglens`，不会重复花钱。有两种翻译方式：

**A. 边看边翻（需要 Anthropic API key）**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run.py
```
浏览器会打开 http://127.0.0.1:8765 。打开哪一页就先翻哪一页，同时预取后面 2 页。

**B. 整本批量翻（走 Claude Code，不需要 API key）**
```bash
python -m maglens.cli_runner --series popeye --model haiku   # 从最新一期往前，一本一本翻
python run.py --manual                                        # 另开一个终端：只读模式看结果
```
它通过 `claude -p` 逐页调用，算在你的 Claude 订阅用量里。用 Haiku 时每页约 $0.01（按标价折算），一本约 1 小时。重跑同一条命令会跳过已经完成的页。

没设 key 且没加 `--manual` 时会进入模拟模式，只显示 PDF 文字层，用来调界面。

## 常用设置

`config.toml` 或命令行：
- `--model` 选模型；`--effort low|medium|high` 在细致程度和花费之间取舍。
- `show_cny`：日元价格后面附上约合人民币。

## 快捷键

←/→ 翻页（默认 → 是下一页）· S 跨页 · 1/2/3/4 整页/适应宽度/适应高度/铺满 · +/−/0 缩放（也可以用 ⌘+滚轮或双指捏合）· B 显示文本框 · R 框选识别 · Esc 取消框选

## 账号与部署

```bash
python -m maglens.users add 你的名字 --role admin   # 有账号之后，所有人都要登录
python -m maglens.users add 朋友                     # 读者只能看已经翻完的杂志，看不到管理功能和花费
python -m maglens.users scope 朋友 <杂志id>          # 限定读者只能看某几期（杂志 id 用 users list 查看）
python -m maglens.publish --out dist                 # 导出预渲染页面 + 精简数据库（不含 PDF）
```

部署到云服务器的完整步骤（轻量服务器 + Caddy HTTPS）见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 版权

这个仓库只有代码，不含任何杂志内容。杂志和译文的版权属于出版方。MagLens 用于私下阅读自己的杂志：部署时请保持登录访问，不要公开，也不要收费。MagLens 网站会要求搜索引擎不要收录。

## 参与开发

```bash
pip install -r requirements-dev.txt
python -m pytest tests            # 用回放的模型响应和假的 claude 命令跑测试，不需要 key
python tests/make_sample.py x.pdf # 生成 3 页测试杂志（虚构内容，不是真实杂志）
```

发现代码问题或者有建议，欢迎[提 issue](https://github.com/Siirui/maglens/issues/new/choose)。如果你是在某个 MagLens 网站上阅读时觉得译文不对，请用译文末尾的「告诉我」反馈。
