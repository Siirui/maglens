"""Render the home-screen icons in web/ from icon A (web/favicon.svg). Dev-only, needs Playwright + Chromium.

python tests/make_icons.py

- icon-192.png, icon-512.png: the favicon as is (rounded red tile, transparent corners), manifest purpose "any".
- icon-maskable-512.png: full-bleed red, artwork scaled into the central 80% safe circle (Android crops it).
- apple-touch-icon.png (180): full-bleed red, artwork unchanged; iOS fills transparency with black and rounds
  the corners itself.
"""
import math
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parent.parent / "web"
RED, CREAM, BLUE = "#E0442B", "#FFF8EC", "#2D5BE3"
# icon A artwork, same geometry as web/favicon.svg (viewBox 0 0 96 96)
ART = (f'<rect x="18" y="14" width="44" height="58" rx="5" fill="{CREAM}" transform="rotate(-7 42 44)"/>'
       f'<circle cx="62" cy="60" r="21" fill="{BLUE}" stroke="{CREAM}" stroke-width="7"/>')


def art_extent():
    """Centre of the artwork's bounding box and the farthest artwork point from it (viewBox units)."""
    a = math.radians(-7)
    pts = []
    for x, y in ((18, 14), (62, 14), (62, 72), (18, 72)):  # page corners (ignoring rx: conservative)
        dx, dy = x - 42, y - 44
        pts.append((42 + dx * math.cos(a) - dy * math.sin(a), 44 + dx * math.sin(a) + dy * math.cos(a)))
    lens = (62, 60, 21 + 3.5)  # radius + half the stroke
    xs = [p[0] for p in pts] + [lens[0] - lens[2], lens[0] + lens[2]]
    ys = [p[1] for p in pts] + [lens[1] - lens[2], lens[1] + lens[2]]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    far = max([math.dist((cx, cy), p) for p in pts] + [math.dist((cx, cy), lens[:2]) + lens[2]])
    return cx, cy, far


def svg(body, size):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" width="{size}" height="{size}">'
            f'{body}</svg>')


def main():
    cx, cy, far = art_extent()
    s = 0.4 * 96 * 0.94 / far  # fit inside the 40%-radius safe circle with a little room
    maskable = (f'<rect width="96" height="96" fill="{RED}"/>'
                f'<g transform="translate(48 48) scale({s:.4f}) translate({-cx:.3f} {-cy:.3f})">{ART}</g>')
    favicon = (WEB / "favicon.svg").read_text(encoding="utf-8")
    jobs = [
        ("icon-192.png", favicon.replace("<svg ", '<svg width="192" height="192" ', 1), 192),
        ("icon-512.png", favicon.replace("<svg ", '<svg width="512" height="512" ', 1), 512),
        ("icon-maskable-512.png", svg(maskable, 512), 512),
        ("apple-touch-icon.png", svg(f'<rect width="96" height="96" fill="{RED}"/>{ART}', 180), 180),
    ]
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        for name, markup, size in jobs:
            page.set_viewport_size({"width": size, "height": size})
            page.set_content(f'<html><body style="margin:0;background:transparent">{markup}</body></html>')
            page.locator("svg").screenshot(path=str(WEB / name), omit_background=True)
            print(name, size)
        b.close()


if __name__ == "__main__":
    main()
