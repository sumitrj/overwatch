"""
Record a short Overwatch demo GIF by driving the live app with Playwright (system Chrome),
then assembling the frames with ffmpeg. Run with the app up on :8000:

    .venv/bin/python scripts/record_demo.py
"""
import pathlib
import subprocess
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAMES = ROOT / "docs" / "_frames"
OUT = ROOT / "docs" / "overwatch-demo.gif"
W, H = 1000, 720

FRAMES.mkdir(parents=True, exist_ok=True)
for old in FRAMES.glob("*.png"):
    old.unlink()

_count = 0
def hold(page, seconds=1.5, fps=2):
    """Capture identical frames so this state 'holds' for `seconds` in the GIF."""
    global _count
    for _ in range(max(1, round(seconds * fps))):
        page.screenshot(path=str(FRAMES / f"f{_count:03d}.png"))
        _count += 1

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": W, "height": H})
    page = ctx.new_page()

    page.goto(f"{BASE}/console")
    page.evaluate("fetch('/reset', {method:'POST'})")
    time.sleep(0.6)

    # 1 · visitor lands — consent banner asks
    page.goto(f"{BASE}/")
    page.wait_for_selector("#consent-banner", state="visible", timeout=6000)
    time.sleep(1.0)
    hold(page, 2.0)

    # 2 · visitor allows — cookie + details kept
    page.click("#consent-banner button")
    time.sleep(1.6)
    hold(page, 2.0)

    # 3 · known email typed — associator will resolve it
    page.fill("#email", "priya@example.com")
    page.eval_on_selector("#email", "el => el.dispatchEvent(new Event('blur'))")
    time.sleep(1.6)
    hold(page, 2.0)

    # 4 · operator opens Overwatch console
    page.goto(f"{BASE}/console")
    time.sleep(2.2)
    hold(page, 2.0)

    # 5 · Sensors — the data layer
    page.evaluate("document.querySelectorAll('.step')[3].scrollIntoView({block:'start'})")
    time.sleep(1.2)
    hold(page, 2.0)

    # 6 · Associators — live vendor match
    page.evaluate("document.querySelectorAll('.step')[4].scrollIntoView({block:'start'})")
    time.sleep(1.2)
    hold(page, 2.0)

    # 7 · the resolved person + raw record
    page.evaluate("var r=document.querySelector('tr[data-id]'); r && r.click()")
    time.sleep(1.0)
    page.evaluate("document.getElementById('person').scrollIntoView({block:'start'})")
    time.sleep(1.2)
    hold(page, 2.5)

    browser.close()

print(f"captured {_count} frames")

pal = FRAMES / "palette.png"
scale = "scale=900:-1:flags=lanczos"
subprocess.run(["ffmpeg", "-y", "-framerate", "2", "-i", str(FRAMES / "f%03d.png"),
                "-vf", f"{scale},palettegen=stats_mode=diff", str(pal)], check=True)
subprocess.run(["ffmpeg", "-y", "-framerate", "2", "-i", str(FRAMES / "f%03d.png"), "-i", str(pal),
                "-lavfi", f"{scale},paletteuse=dither=bayer:bayer_scale=3", str(OUT)], check=True)

for f in FRAMES.glob("*.png"):
    f.unlink()
FRAMES.rmdir()
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
