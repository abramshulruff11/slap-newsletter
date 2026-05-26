"""
Render the per-sport box score HTML files to cropped PNGs for Substack.

Used both locally and in CI. Renders each box_score_sport_*.html with Chromium
via Playwright (full-page, locked 400px width, 2x scale for crisp text), then
trims top/bottom whitespace with Pillow so each image is tight to its content.

Browser: prefers the system Google Chrome (channel="chrome", typical locally)
and falls back to Playwright's bundled Chromium (CI installs it with
`python -m playwright install chromium`). Both are the same engine, so the
output matches regardless of environment.

email_newsletter.py attaches the resulting box_score_sport_*.png files in
sorted (numeric-prefix) order.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image, ImageChops

HERE = Path(__file__).resolve().parent
PAD = 16  # ~8 CSS px of breathing room at 2x scale


def _launch(p):
    """System Chrome if present (local), else bundled Chromium (CI)."""
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


def _crop_whitespace(path: Path):
    """Trim blank space from the top and bottom; keep full width for uniformity."""
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bbox = ImageChops.difference(im, bg).getbbox()  # (left, top, right, bottom)
    if not bbox:
        return
    top = max(0, bbox[1] - PAD)
    bottom = min(im.height, bbox[3] + PAD)
    im.crop((0, top, im.width, bottom)).save(path)


def main():
    files = sorted(HERE.glob("box_score_sport_*.html"))
    if not files:
        print("No box_score_sport_*.html files found — nothing to render.")
        return
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 400, "height": 1000}, device_scale_factor=2)
        for f in files:
            page.goto(f.as_uri())
            page.wait_for_timeout(150)
            out = f.with_suffix(".png")
            page.screenshot(path=str(out), full_page=True)
            _crop_whitespace(out)
            print(f"  {out.name:32s} {out.stat().st_size // 1024} KB")
        browser.close()
    print("Done.")


if __name__ == "__main__":
    main()
