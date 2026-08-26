"""
THROWAWAY PROBE — GIF quality matrix at a higher budget.

Research settled two things:
  - GIF stays (WebP breaks in Gmail/Outlook; video won't autoplay in email).
  - The real size ceiling is beehiiv's 5MB / Substack's 10MB, NOT the 2MB the
    ladder currently targets. At 2MB the ladder strips palette to 64 colors and
    leans on bayer dithering — that crosshatch + banding IS the graininess.

This renders ONE clip (the walk-off, which motion detection lands correctly)
across a matrix of recipes and prints the file size next to each, so you can
eyeball the quality/size tradeoff and pick a target recipe before we touch the
production ladder.

The window is held constant (peak-motion 6s) across every recipe, so the ONLY
thing changing between outputs is the encode — a fair quality comparison.

Recipes:
  baseline_current   what the ladder ships now  (64c, bayer, 360px, 6s)  -> grainy
  256_sierra_400_6s  256 colors, error-diffusion dither, 400px, 6s
  256_floyd_400_6s   same but floyd_steinberg dither (heavier diffusion)
  256_sierra_480_6s  push resolution to 480px (the "spend to the ceiling" one)
  256_sierra_crop_6s 256c 480px but center-cropped to 70% (fewer pixels, tighter)
  256_sierra_400_4s  256c 400px but 4s @ 15fps (shorter + crisper per frame)

Run:  python uat/probe_quality_matrix.py
Then open uat/probe/quality/ and compare. Paste the size table back and tell me
which recipe looks clean AND lands under ~4.5MB (beehiiv-safe with margin).

No API keys — just yt-dlp + ffmpeg. Writes only to uat/probe/.
"""

from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from highlight_to_gif import FFMPEG, FFPROBE, tools_available, probe, clip_window, MAX_CLIP_SEC
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "Could not import from highlight_to_gif.py — run from the repo so "
        "uat/highlight_to_gif.py is importable.  Error: %s" % e
    )

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROBE_DIR = SCRIPT_DIR / "probe"
OUT_DIR   = PROBE_DIR / "quality"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# The walk-off — motion detection lands on the swing, so quality is the only
# variable left. Swap this URL to test a different scene (e.g. the WNBA clip).
CLIP_ID  = "play_ll_walkoff"
CLIP_URL = "https://twitter.com/SportsCenter/status/2090244505668563067"

# beehiiv is the tightest real ceiling (5MB). Flag anything at/over 4.5MB as
# "no margin" and anything over 5.0MB as "beehiiv-unsafe".
SAFE_MB     = 4.5
BEEHIIV_MB  = 5.0

# (label, fps, width, colors, dither, dither_param, crop_frac, seconds)
RECIPES = [
    ("baseline_current",   10, 360,  64, "bayer",        3,    None, 6.0),
    ("256_sierra_400_6s",  12, 400, 256, "sierra2_4a",   None, None, 6.0),
    ("256_floyd_400_6s",   12, 400, 256, "floyd_steinberg", None, None, 6.0),
    ("256_sierra_480_6s",  12, 480, 256, "sierra2_4a",   None, None, 6.0),
    ("256_sierra_crop_6s", 12, 480, 256, "sierra2_4a",   None, 0.70, 6.0),
    ("256_sierra_400_4s",  15, 400, 256, "sierra2_4a",   None, None, 4.0),
]


def _run(cmd: list, timeout: int = 420) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def download_reuse(url: str, hid: str) -> Path | None:
    existing = sorted(PROBE_DIR.glob("src_" + hid + ".*"))
    if existing:
        print("  (reusing existing download: %s)" % existing[0].name)
        return existing[0]
    url = url.split("#")[0]
    out_tmpl = str(PROBE_DIR / ("src_" + hid + ".%(ext)s"))
    proc = _run([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--no-playlist",
                 "-f", "best[width<=480]/best[height<=480]/best[height<=720]/best",
                 "-o", out_tmpl, url])
    if proc.returncode != 0:
        return None
    files = sorted(PROBE_DIR.glob("src_" + hid + ".*"))
    return files[0] if files else None


def motion_profile(src: Path, hid: str) -> list[tuple[float, float]]:
    """Per-frame mean absolute frame difference via ffmpeg scdet.

    Reads scores from STDERR via metadata=print — NOT metadata=print:file=<path>.
    A Windows path carries a drive-letter colon (C:\\...) that ffmpeg's
    filtergraph parser reads as an option separator, breaking the filter
    ("Error opening output files: Invalid argument"). No -loglevel error here,
    or the metadata lines we parse would be suppressed.
    """
    p = _run([FFMPEG, "-hide_banner", "-i", str(src),
              "-an", "-vf", "scdet=s=0,metadata=print",
              "-f", "null", "-"])
    text = p.stderr or ""
    if "scd.mafd" not in text:
        return []
    prof, cur_t = [], None
    for line in text.splitlines():
        mt = re.search(r"pts_time:([\d.]+)", line)
        if mt:
            cur_t = float(mt.group(1)); continue
        mm = re.search(r"lavfi\.scd\.mafd=([\d.]+)", line)
        if mm and cur_t is not None:
            prof.append((cur_t, float(mm.group(1))))
    return prof


def peak_motion_start(prof: list[tuple[float, float]], dur: float, want: float) -> float:
    if dur <= want or not prof:
        return 0.0
    best_start, best_score, start = 0.0, -1.0, 0.0
    last = max(dur - want, 0.0)
    while start <= last + 1e-9:
        s = sum(m for t, m in prof if start <= t < start + want)
        if s > best_score:
            best_score, best_start = s, start
        start = round(start + 0.5, 2)
    return round(best_start, 2)


def encode_recipe(src: Path, out: Path, start: float, seconds: float,
                  fps: int, width: int, colors: int, dither: str,
                  dither_param, crop_frac, tag: str) -> bool:
    """Two-pass palettegen/paletteuse with configurable dither + optional
    centered crop. crop happens before scale so we drop pixels, then resize."""
    def chain() -> str:
        parts = ["fps=%d" % fps]
        if crop_frac:
            parts.append("crop=iw*%.2f:ih*%.2f" % (crop_frac, crop_frac))
        parts.append("scale=%d:-1:flags=lanczos" % width)
        return ",".join(parts)

    palette = out.parent / ("pal_" + tag + ".png")
    vf_gen = chain() + ",palettegen=max_colors=%d:stats_mode=diff" % colors
    p1 = _run([FFMPEG, "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-t", "%.2f" % seconds, "-i", str(src),
               "-vf", vf_gen, "-y", str(palette)])
    if p1.returncode != 0:
        return False

    duse = "dither=" + dither
    if dither == "bayer" and dither_param is not None:
        duse += ":bayer_scale=%d" % dither_param
    lavfi = chain() + "[x];[x][1:v]paletteuse=" + duse
    p2 = _run([FFMPEG, "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-t", "%.2f" % seconds, "-i", str(src),
               "-i", str(palette), "-lavfi", lavfi, "-y", str(out)])
    palette.unlink(missing_ok=True)
    return p2.returncode == 0 and out.is_file()


def main() -> None:
    ok, msg = tools_available()
    print("tooling:", msg)
    if not ok:
        raise SystemExit("Install the missing tools and re-run.")

    print("\n" + "=" * 66)
    print("GIF QUALITY MATRIX  —  clip: %s" % CLIP_ID)
    print("=" * 66)

    src = download_reuse(CLIP_URL, CLIP_ID)
    if not src:
        raise SystemExit("yt-dlp could not fetch the clip.")

    dur, w, h = probe(src)
    if dur <= 0:
        raise SystemExit("ffprobe could not read the source.")
    print("  source: %dx%d, %.2fs" % (w, h, dur))

    prof = motion_profile(src, CLIP_ID)
    if prof:
        start6 = peak_motion_start(prof, dur, MAX_CLIP_SEC)
        print("  peak-motion window start: %.1fs (holding constant across recipes)" % start6)
    else:
        start6, _ = clip_window(dur, MAX_CLIP_SEC)
        print("  ⚠ motion pass unavailable — falling back to last-6s window @ %.1fs" % start6)

    results = []
    for (label, fps, width, colors, dither, dparam, crop, secs) in RECIPES:
        # A 4s recipe re-centers its shorter window on the same motion peak.
        if abs(secs - MAX_CLIP_SEC) > 0.1 and prof:
            start = peak_motion_start(prof, dur, secs)
        else:
            start = min(start6, max(dur - secs, 0.0))
        out = OUT_DIR / (CLIP_ID + "__" + label + ".gif")
        okr = encode_recipe(src, out, start, secs, fps, width, colors,
                            dither, dparam, crop, label)
        size_mb = (out.stat().st_size / 1048576) if (okr and out.is_file()) else None
        results.append((label, fps, width, colors, dither, secs, crop, size_mb))
        if size_mb is None:
            print("  [%-20s] ENCODE FAILED" % label)
        else:
            print("  [%-20s] %5.2f MB" % (label, size_mb))

    # ---- summary ----------------------------------------------------------
    print("\n" + "=" * 66)
    print("SUMMARY  (target: clean AND <= %.1f MB for beehiiv margin)" % SAFE_MB)
    print("=" * 66)
    print("%-20s %4s %4s %4s %-14s %4s %5s %8s  %s"
          % ("recipe", "fps", "w", "col", "dither", "sec", "crop", "size", "verdict"))
    for label, fps, width, colors, dither, secs, crop, size_mb in results:
        if size_mb is None:
            verdict = "FAILED"
            size_s = "—"
        else:
            size_s = "%.2fMB" % size_mb
            if size_mb <= SAFE_MB:
                verdict = "OK (margin)"
            elif size_mb <= BEEHIIV_MB:
                verdict = "tight (no margin)"
            else:
                verdict = "beehiiv-UNSAFE"
        print("%-20s %4d %4d %4d %-14s %4.1f %5s %8s  %s"
              % (label, fps, width, colors, dither, secs,
                 ("%.0f%%" % (crop * 100)) if crop else "full", size_s, verdict))

    print("\nOpen:  %s" % OUT_DIR)
    print("Compare baseline_current (grainy) against the 256-color recipes.")
    print("Pick the one that looks clean and stays under %.1f MB." % SAFE_MB)


if __name__ == "__main__":
    main()
