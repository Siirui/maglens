[简体中文](README.md) · **English**

# MagLens

A reader for Japanese magazines with a Chinese translation beside every page. Each page is read by a Claude vision model, cross-checked against the PDF's text layer to catch anything it missed, and translated into the voice of a Chinese lifestyle magazine. The magazine stays the main thing on screen; the translation is there when you need it.

## What it does

- **Original and translation side by side.** Hover or tap a passage on the page to highlight its translation, and the other way round. Switch between Chinese, bilingual and Japanese only.
- **Reads like a magazine.** Pages turn the way Chinese books do by default (next page on the right, or switch to Japanese order), two-page spreads kept in the magazine's own right-bound layout, fit modes, zoom, swipe to turn pages, and the next pages preloaded.
- **Phones.** In portrait the page fills the screen and the translation sits in a bottom sheet with three heights. An optional tap mode shows a passage's translation in a bubble right on the page.
- **Batch translation that resumes.** Translate a whole issue in the background; interrupted runs pick up where they stopped, and pages that are already done are never paid for twice.
- **A glossary per series**, so names of shops, brands and people stay consistent from issue to issue.
- **Small private deployments.** Accounts with reader and admin roles, per-account access to chosen issues, and in-app feedback from readers. The server only serves pre-rendered images and finished translations: no PDFs, and it never calls a model.

## Run it on a Mac (Python ≥ 3.11)

```bash
git clone https://github.com/Siirui/maglens.git && cd maglens
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Point `[library] dir` in `config.toml` at your folder of magazine PDFs (or pass `--dir`). Folders of JPG scans are converted to PDF in `~/.maglens/converted/`; the originals are left alone.

Translations are stored in `~/.maglens` and reused. There are two ways to produce them:

**A. Translate as you read (needs an Anthropic API key)**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run.py
```
Your browser opens http://127.0.0.1:8765. The page you open is translated first, and the next two are prefetched.

**B. Translate whole issues in the background (uses Claude Code, no API key)**
```bash
python -m maglens.cli_runner --series popeye --model haiku   # newest issue first, one issue at a time
python run.py --manual                                        # in another terminal: read-only viewer
```
This calls `claude -p` page by page, so it counts against your Claude plan. With Haiku a page costs about $0.01 at list price and an issue takes about an hour. Running the same command again skips finished pages.

Without a key and without `--manual`, MagLens runs in mock mode and only shows the PDF's text layer, which is handy for working on the interface.

## Settings

In `config.toml` or on the command line:
- `--model` picks the model; `--effort low|medium|high` trades detail for cost.
- `show_cny` adds an approximate RMB amount after Japanese prices.

## Keyboard

←/→ turn pages (→ is next by default) · S spread · 1/2/3/4 page / width / height / fill · +/−/0 zoom (also ⌘ + scroll or pinch) · B show text boxes · R select a region to re-read · Esc cancel

## Accounts and deployment

```bash
python -m maglens.users add yourname --role admin    # once there is an account, everyone has to log in
python -m maglens.users add friend                   # readers only see finished issues, no admin tools or costs
python -m maglens.users scope friend <issue id>      # limit a reader to some issues (ids: users list)
python -m maglens.publish --out dist                 # pre-rendered pages + a slim database, no PDFs
```

[docs/DEPLOY.md](docs/DEPLOY.md) walks through a full setup on a small cloud server with Caddy and HTTPS (written in Chinese).

## Copyright

This repository contains code only, no magazine content. Magazines and their translations belong to their publishers. MagLens is meant for reading your own copies privately: keep deployments behind a login, don't open them to the public, and don't charge for access. Search engines are told not to index MagLens sites.

## Contributing

```bash
pip install -r requirements-dev.txt
python -m pytest tests            # replays recorded model responses and a fake claude command; no key needed
python tests/make_sample.py x.pdf # builds a 3-page sample magazine (made-up text, not a real issue)
```

Found a bug in the code or have an idea? [Open an issue](https://github.com/Siirui/maglens/issues/new/choose). If you're reading on a MagLens site and a translation looks wrong, use the feedback link at the end of the translation panel instead.
