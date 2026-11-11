"""Generate a 3-page fake Japanese magazine PDF for testing (original content).

p1: feature opener (vertical title, lead, photo, captions)    – image + invisible text layer
p2: vertical multi-column body + shop info sidebar + captions – image + invisible text layer
p3: product listing with prices                                – image only (simulates a scan)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

import os

_FONT_CANDIDATES = [
    os.environ.get("MAGLENS_TEST_FONT", ""),
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",  # Linux (fonts-ipafont-gothic)
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS
]
FONT = next((c for c in _FONT_CANDIDATES if c and Path(c).exists()), _FONT_CANDIDATES[1])
W, H = 1240, 1680  # px, ~A4 at 150dpi
PT = 595 / W       # px -> pt


def f(sz):
    return ImageFont.truetype(FONT, sz)


class Page:
    def __init__(self, bg="#fbfaf6"):
        self.img = Image.new("RGB", (W, H), bg)
        self.d = ImageDraw.Draw(self.img)
        self.spans = []  # (text, x, y, size, vertical)

    def h(self, text, x, y, sz, fill="#111", layer=True):
        self.d.text((x, y), text, font=f(sz), fill=fill)
        if layer:
            self.spans.append((text, x, y, sz, False))

    def v(self, text, x, y, sz, fill="#111", layer=True, lh=1.0):
        """Vertical column, top->bottom; returns bottom y."""
        cy = y
        for ch in text:
            if ch in "ー〜…":
                tmp = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
                ImageDraw.Draw(tmp).text((0, 0), ch, font=f(sz), fill=fill)
                self.img.paste(tmp.rotate(-90), (x, cy), tmp.rotate(-90))
            elif ch in "、。":
                self.d.text((x + sz * 0.55, cy - sz * 0.45), ch, font=f(sz), fill=fill)
            else:
                self.d.text((x, cy), ch, font=f(sz), fill=fill)
            cy += int(sz * lh)
        if layer:
            self.spans.append((text, x, y, sz, True))
        return cy

    def vcols(self, text, x_right, y, sz, rows, gap, ncols_max=40, layer=True):
        """Flow text into vertical columns right-to-left. Returns x of last column."""
        cols = [text[i:i + rows] for i in range(0, len(text), rows)][:ncols_max]
        x = x_right
        for c in cols:
            self.v(c, x, y, sz, layer=layer)
            x -= sz + gap
        return x

    def photo(self, box, color, label=""):
        x0, y0, x1, y1 = box
        self.d.rectangle(box, fill=color)
        for i in range(0, x1 - x0, 24):
            self.d.line([(x0 + i, y0), (x0, y0 + i)], fill=tuple(min(255, c + 12) for c in color), width=2)
        if label:
            self.h(label, x0 + 20, y0 + 20, 22, fill="#fff", layer=False)


def build_pdf(pages: list[Page], out: Path, text_layer: list[bool]):
    doc = pymupdf.open()
    for pg, layer in zip(pages, text_layer):
        p = doc.new_page(width=W * PT, height=H * PT)
        buf = io.BytesIO()
        pg.img.save(buf, "JPEG", quality=88)
        p.insert_image(p.rect, stream=buf.getvalue())
        if layer:
            p.insert_font(fontname="ipag", fontfile=FONT)
            for text, x, y, sz, vert in pg.spans:
                if vert:  # one line per vertical column, like OCR'd magazine PDFs
                    for i, ch in enumerate(text):
                        p.insert_text((x * PT, (y + sz * (i + 0.9)) * PT), ch, fontname="ipag",
                                      fontsize=sz * PT, render_mode=3)
                else:
                    p.insert_text((x * PT, (y + sz * 0.9) * PT), text, fontname="ipag",
                                  fontsize=sz * PT, render_mode=3)
    doc.save(out)


def page1() -> Page:
    p = Page()
    p.photo((0, 0, W, 980), (96, 118, 104), "PHOTO")
    p.h("CITY BOY'S GUIDE", 60, 60, 34, fill="#fff")
    p.v("高円寺で、", 1100, 1020, 64)
    p.v("古着と珈琲の", 1010, 1020, 64)
    p.v("一日。", 920, 1020, 64)
    lead = ("駅を出て北口の商店街を抜けると、そこはもう古着屋の迷路。"
            "今回は、店主たちが本当に通っている喫茶店と、週末だけ開く小さなショップを"
            "朝から晩まで歩いて巡ってみた。")
    p.vcols(lead, 820, 1030, 28, 20, 14)
    p.h("① 北口の純情商店街。朝十時、シャッターが上がり始める。", 60, 1500, 22)
    p.h("② 古着屋「ハイウェイ」の店先に並ぶ、九〇年代のワークジャケット。", 60, 1540, 22)
    p.h("写真・中村 圭　文・田辺 ゆう", 60, 1610, 18, fill="#666")
    p.h("12", 1160, 1630, 18, fill="#666")
    return p


def page2() -> Page:
    p = Page()
    p.h("CITY BOY'S GUIDE ─ 高円寺", 60, 40, 20, fill="#666")
    p.v("喫茶ミロンガで", 1130, 110, 44)
    p.v("ナポリタンを。", 1070, 110, 44)
    body = ("創業四十年を超える喫茶ミロンガは、古着屋の店主たちが仕入れ帰りに必ず寄る場所だ。"
            "厚手のカップで出てくるブレンドは少し苦めで、ケチャップたっぷりのナポリタンとよく合う。"
            "マスターの佐藤さんいわく「流行りは追わない。毎日同じ味を出すのが一番難しいんだよ」。"
            "壁には常連が置いていったレコードがずらりと並び、リクエストすればかけてくれる。"
            "平日の昼過ぎなら、窓際の席が空いていることが多い。")
    p.vcols(body, 990, 110, 26, 26, 12)
    p.photo((60, 110, 560, 760), (150, 96, 70), "PHOTO")
    p.h("③ 看板メニューのナポリタン（950円）。", 60, 780, 22)
    p.h("④ マスターの佐藤 正さん。この道四十年。", 60, 815, 22)
    # sidebar shop info
    p.d.rectangle((60, 1180, 1180, 1560), outline="#111", width=2)
    p.h("SHOP DATA", 90, 1200, 22)
    p.h("喫茶 ミロンガ", 90, 1240, 30)
    info = ["東京都杉並区高円寺北3-XX-X", "☎ 03-XXXX-XXXX",
            "営業時間：9:00〜19:00（L.O. 18:30）", "定休日：水曜",
            "ブレンドコーヒー 600円（税込）／ナポリタン 950円（税込）"]
    for i, t in enumerate(info):
        p.h(t, 90, 1295 + i * 44, 24)
    p.h("13", 60, 1630, 18, fill="#666")
    return p


def page3() -> Page:
    p = Page(bg="#f2efe6")
    p.h("週末だけ開く店で見つけた、5つのいいもの。", 60, 60, 40)
    p.h("土日しか開かない「ウィークエンド・ストア」は、店主の小林さんが平日に会社員をしながら営む小さな店。", 60, 130, 22)
    p.h("セレクトは独特だけど、どれも長く使えるものばかり。", 60, 165, 22)
    items = [
        ("A", "フランス軍のモールスキンジャケット", "50年代のデッドストック。", "28,000円"),
        ("B", "手編みのフィッシャーマンセーター", "アイルランドの小さな工房製。", "19,800円"),
        ("C", "真鍮のキーリング", "使うほどに味が出る。", "3,300円"),
        ("D", "古いアメリカの絵本", "表紙のイラストに一目惚れ。", "4,500円"),
        ("E", "琺瑯のマグカップ", "キャンプにも、家でも。", "2,200円"),
    ]
    for i, (k, name, desc, price) in enumerate(items):
        col, row = i % 2, i // 2
        x, y = 60 + col * 600, 230 + row * 450
        p.photo((x, y, x + 540, y + 300), (120 + i * 18, 110, 140 - i * 10), "")
        p.h(k, x + 14, y + 10, 36, fill="#fff")  # letter inside photo
        p.h(name, x, y + 315, 24)
        p.h(desc, x, y + 350, 19, fill="#444")
        p.h(price, x, y + 380, 19)  # small price text — easy to miss
    p.d.rectangle((660, 1130, 1200, 1430), fill="#222")
    p.h("SALE", 690, 1150, 70, fill="#f5d000")  # text inside an image region
    p.h("毎月第一土曜は全品10%オフ", 690, 1240, 26, fill="#fff")
    p.h("ウィークエンド・ストア　東京都杉並区高円寺南2-XX-X　土・日 12:00〜20:00", 60, 1560, 18)
    p.h("問い合わせ：weekendstore@example.jp", 60, 1590, 18)
    p.h("14", 1160, 1630, 18, fill="#666")
    return p


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "SAMPLE 2026年1月号.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    build_pdf([page1(), page2(), page3()], out, [True, True, False])
    print("wrote", out)
