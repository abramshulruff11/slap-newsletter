"""
THROWAWAY PROBE — is the SOURCE resolution the real bottleneck?

The quality matrix rendered every recipe from a 480x270 source and they all
looked meh. That source is 480x270 because production asks for it that way:

    YT_DLP_FORMAT = "best[width<=480]/..."   <-- caps the download at 480px wide

Twitter/X usually hosts higher renditions (SportsCenter posts are often 720p).
GIF palette tricks can't add detail the source never had. This probe tests
whether pulling a SHARPER source — then downscaling to the same 400px GIF —
fixes the graininess for free (GIF size is set by OUTPUT dims, not source).

It:
  1. dumps `yt-dlp -F` so we see what resolutions this clip actually offers,
  2. downloads the walk-off at 3 tiers: cap480 (current), cap720, best,
  3. renders each to the IDENTICAL 400px / 256c / sierra2_4a / 6s GIF at the
     same peak-motion window (only the SOURCE differs),
  4. prints source dims + GIF size per tier.

Run:  python uat/probe_source_res.py
Open uat/probe/source/ and compare walkoff__cap480.gif vs __cap720 vs __best.
If __best looks noticeably sharper at the same size, the fix is a one-line
YT_DLP_FORMAT change. If all three are the same file/size, Twitter only has a
low-res rendition and GIF genuinely can't carry these clips — which points at
the video-on-web path instead.

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
    from highlight_to_gif import FFMPEG, FFPROBE, tools_available, probe, MAX_CLIP_SEC
except Exception as e:  # pragma: no cover
    raise SystemExit("Import from highlight_to_gif.py failed — run from repo. %s" % e)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROBE_DIR = SCRIPT_DIR / "probe"
OUT_DIR   = PROBE_DIR / "source"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CLIP_ID  = "walkoff"
CLIP_URL = "https://twitter.com/SportsCenter/status/2090244505668563067"

# Fixed OUTPUT recipe — held constant so the only variable is source resolution.
OUT_FPS, OUT_W, OUT_COLORS, OUT_SECS, OUT_DITHER = 12, 400, 256, 6.0, "sierra2_4a"

# (tier label, yt-dlp format string)
TIERS = [
    ("cap480_current", "best[width<=480]/best[height<=480]/best[height<=720]/best"),
    ("cap720",         "best[width<=720]/best[height<=720]/best"),
    ("best",           "best"),
]


def _run(cmd: list, timeout: int = 420) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def list_formats(url: str) -> None:
    print("\n--- yt-dlp -F (available renditions) " + "-" * 30)
    p = _run([sys.executable, "-m", "yt_dlp", "-F", "--no-warnings",
              url.split("#")[0]])
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        # keep the format table rows, skip the noise
        if re.search(r"\b(mp4|hls|http|\d{3,4}x\d{3,4}|ID\s+EXT)\b", line):
            print("   " + line)
    print("-" * 66)


def download_tier(url: str, tier: str, fmt: str) -> Path | None:
    out_tmpl = str(OUT_DIR / ("src_" + CLIP_ID + "_" + tier + ".%(ext)s"))
    for f in OUT_DIR.glob("src_" + CLIP_ID + "_" + tier + ".*"):
        f.unlink(missing_ok=True)
    p = _run([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--no-playlist",
              "-f", fmt, "-o", out_tmpl, url.split("#")[0]])
    if p.returncode != 0:
        return None
    files = sorted(OUT_DIR.glob("src_" + CLIP_ID + "_" + tier + ".*"))
    return files[0] if files else None


def motion_profile(src: Path) -> list[tuple[float, float]]:
    p = _run([FFMPEG, "-hide_banner", "-i", str(src),
              "-an", "-vf", "scdet=s=0,metadata=print", "-f", "null", "-"])
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


def peak_motion_start(prof, dur: float, want: float) -> float:
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


def encode(src: Path, out: Path, start: float, secs: float, tag: str) -> bool:
    chain = "fps=%d,scale=%d:-1:flags=lanczos" % (OUT_FPS, OUT_W)
    palette = out.parent / ("pal_" + tag + ".png")
    p1 = _run([FFMPEG, "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-t", "%.2f" % secs, "-i", str(src),
               "-vf", chain + ",palettegen=max_colors=%d:stats_mode=diff" % OUT_COLORS,
               "-y", str(palette)])
    if p1.returncode != 0:
        return False
    lavfi = chain + "[x];[x][1:v]paletteuse=dither=" + OUT_DITHER
    p2 = _run([FFMPEG, "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-t", "%.2f" % secs, "-i", str(src),
               "-i", str(palette), "-lavfi", lavfi, "-y", str(out)])
    palette.unlink(missing_ok=True)
    return p2.returncode == 0 and out.is_file()


def main() -> None:
    ok, msg = tools_available()
    print("tooling:", msg)
    if not ok:
        raise SystemExit("Install the missing tools and re-run.")

    print("\n" + "=" * 66)
    print("SOURCE-RESOLUTION PROBE  —  same 400px GIF, 3 source tiers")
    print("=" * 66)

    list_formats(CLIP_URL)

    downloads = []
    for tier, fmt in TIERS:
        src = download_tier(CLIP_URL, tier, fmt)
        if not src:
            print("  [%-15s] download FAILED (format %s)" % (tier, fmt))
            downloads.append((tier, None, None))
            continue
        dur, w, h = probe(src)
        size_mb = src.stat().st_size / 1048576
        print("  [%-15s] source %dx%d, %.2fs, %.2fMB dl  (%s)"
              % (tier, w, h, dur, size_mb, src.suffix))
        downloads.append((tier, src, (dur, w, h)))

    # Pick the motion window on the sharpest source we actually got, reuse for all.
    ref = next((s for _, s, _ in downloads if s), None)
    if not ref:
        raise SystemExit("No source downloaded — cannot proceed.")
    rdur, _, _ = probe(ref)
    prof = motion_profile(ref)
    start = peak_motion_start(prof, rdur, OUT_SECS) if prof else max(rdur - OUT_SECS, 0.0)
    print("\n  motion window start: %.1fs (held constant across tiers)" % start)

    print("\n" + "=" * 66)
    print("RESULTS  —  identical 400px/256c/%s/%.0fs output" % (OUT_DITHER, OUT_SECS))
    print("=" * 66)
    print("%-15s %-12s %-10s  %s" % ("tier", "source", "gif size", "output"))
    for tier, src, dims in downloads:
        if not src:
            print("%-15s %-12s %-10s  %s" % (tier, "—", "—", "download failed"))
            continue
        dur, w, h = dims
        s = min(start, max(dur - OUT_SECS, 0.0))
        out = OUT_DIR / (CLIP_ID + "__" + tier + ".gif")
        good = encode(src, out, s, OUT_SECS, tier)
        size = "%.2fMB" % (out.stat().st_size / 1048576) if good and out.is_file() else "FAILED"
        print("%-15s %-12s %-10s  %s"
              % (tier, "%dx%d" % (w, h), size, out.name))

    print("\nOpen:  %s" % OUT_DIR)
    print("Compare  %s__cap480_current.gif  vs  __cap720  vs  __best." % CLIP_ID)
    print("Same size, same window — if __best is sharper, the fix is one line.")


if __name__ == "__main__":
    main()
