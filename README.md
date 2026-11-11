# MagLens — 本地日文杂志阅读 + 翻译

左边看杂志页面，右边看中文译文。用 Claude 视觉模型识别整页图片，再拿 PDF 文字层交叉核对、补上漏掉的部分，翻译成大陆生活方式杂志的中文风格。

![对照模式](docs/screenshots/3_page2_bilingual_hover.png)

## 在 Mac 上运行（Python ≥ 3.11）
```bash
git clone https://github.com/Siirui/maglens.git && cd maglens
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python run.py --dir ~/Downloads/2026
```
浏览器会自动打开 http://127.0.0.1:8765 。只有打开某一页才会翻译这一页（并在后台预取后面 2 页），不会一次性把整本都翻完。结果缓存在 `~/.maglens`，同一页不会重复花钱。

没设 key 时会进入模拟模式，只显示 PDF 文字层，用来检查界面是否正常。

## 常用设置（config.toml / 命令行）
- `--model claude-sonnet-5-5` 或 `claude-haiku-5-5`：更便宜；默认 `claude-opus-5-5` 效果最好
- `--effort low|medium|high`：识别的细致程度
- `show_cny`：价格后面附上约合人民币

## 快捷键
←/→ 翻页（默认右翻，跟日本杂志一样）· S 跨页 · B 显示文本框 · R 框选识别 · Esc 取消框选

## 开发
```bash
python -m pytest tests            # 用回放的模型响应跑测试，不需要 key
python tests/make_sample.py x.pdf # 生成 3 页测试杂志
```
