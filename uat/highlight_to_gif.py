"""
UAT-only: tweet video -> short silent looping GIF.

Clip selection is deterministic. The model never sees the video, so it never
picks the timestamp: source <= MAX_CLIP_SEC is used whole, anything longer is
cut to its LAST MAX_CLIP_SEC seconds on the theory that highlight clips are
edited so the payoff lands at the end. Every source duration and chosen window
is logged so that rule can be judged against real output.

Sizing note (measured, not assumed): high-motion sports footage is the worst
case for GIF -- every frame is unique, so there is no interframe compression to
win. The spec's fixed ladder (fps 15->12->10, width 480->400->360) bottoms out
around 5.8 MB on a 6s portrait clip, ~3x over the 2 MB budget. Reaching 2 MB
needs two levers the spec omits: palette size (max_colors) and clip duration.
Portrait is the hard case -- at width 480 a 9:16 frame carries ~3x the pixel
area of a 16:9 one.

Failure is never fatal: a clip that cannot be fetched or encoded is dropped from
the plan and the run continues. A broken <img> is worse than a missing one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# -- Tunables ---------------------------------------------------------------
MAX_CLIP_SEC   = 6.0
MIN_CLIP_SEC   = 3.0
SIZE_BUDGET_MB = 2.0

# Quality ladder, best first. Each rung is (fps, width, colors, seconds).
# Rung 0 is the spec's default; everything below it is what measurement showed
# is actually needed. Duration only starts dropping once the cheap visual levers
# are spent, since a truncated payoff is worse than a grainy one.
LADDER: list[tuple[int, int, int, float]] = [
    (15, 480, 256, 6.0),
    (12, 480, 128, 6.0),
    (12, 400, 128, 6.0),
    (10, 400,  96, 6.0),
    (10, 360,  64, 6.0),
    (10, 360,  64, 5.0),
    (10, 320,  64, 4.0),
    (10, 320,  64, 3.0),
    ( 8, 300,  64, 3.0),
]

# A clip shorter than MIN_CLIP_SEC stops reading as a replay and starts reading
# as a glitch, so the ladder trades resolution away before it trades length.
assert min(rung[3] for rung in LADDER) >= MIN_CLIP_SEC, \
    "LADDER would cut clips below MIN_CLIP_SEC"

# Bytes per pixel-frame, measured across portrait and landscape sports clips.
# Palette size is the dominant term: 256-colour encodes landed near 0.50, and
# 64-colour ones near 0.35-0.39. Used only to pick a starting rung so most clips
# encode once or twice instead of walking the whole ladder.
def _density(colors: int) -> float:
    return 0.30 + 0.20 * (colors / 256.0)

# Mean luma below which a first frame is treated as black/transition. Outlook
# desktop renders only frame one, statically, so a black frame reads as broken.
DARK_FRAME_YAVG = 26.0
DARK_FRAME_NUDGE_SEC = 0.4
DARK_FRAME_MAX_NUDGES = 3

# Vertical video is common on highlight accounts, and best[height<=480] matches
# NOTHING on a 1080x1920 source (min height 568) -- yt-dlp then errors out.
YT_DLP_FORMAT = "best[width<=480]/best[height<=480]/best[height<=720]/best"


# -- Tool discovery ---------------------------------------------------------

def _find_binary(name: str) -> str | None:
    """Locate ffmpeg/ffprobe. winget installs them off-PATH for existing shells."""
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates: list = []
    if local:
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / (name + ".exe"))
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.is_dir():
            candidates += list(pkgs.glob("*FFmpeg*/**/bin/" + name + ".exe"))
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    return None


FFMPEG  = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")


def tools_available() -> tuple[bool, str]:
    missing = [n for n, p in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)) if not p]
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       capture_output=True, check=True, timeout=60)
    except Exception:
        missing.append("yt-dlp")
    if missing:
        return False, ("missing: " + ", ".join(missing)
                       + "  (pip install yt-dlp / winget install Gyan.FFmpeg)")
    return True, "ffmpeg + ffprobe + yt-dlp present"


def _run(cmd: list, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# -- Result record ----------------------------------------------------------

@dataclass
class ClipResult:
    highlight_id: str
    source_url: str
    ok: bool
    reason: str = ""
    output_file: str = ""
    source_duration: float = 0.0
    source_dims: str = ""
    clip_start: float = 0.0
    clip_len: float = 0.0
    final_fps: int = 0
    final_width: int = 0
    final_colors: int = 0
    size_mb: float = 0.0
    step_downs: int = 0
    ladder_log: list = field(default_factory=list)
    first_frame_yavg: float | None = None
    dark_frame_nudges: int = 0


# -- Pipeline steps ---------------------------------------------------------

def download(url: str, media_dir: Path, hid: str) -> Path | None:
    # Nitter-derived links carry a "#m" fragment; strip it before handing to yt-dlp.
    url = url.split("#")[0]
    out_tmpl = str(media_dir / ("src_" + hid + ".%(ext)s"))
    for f in media_dir.glob("src_" + hid + ".*"):
        f.unlink(missing_ok=True)
    proc = _run([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings",
                 "--no-playlist", "-f", YT_DLP_FORMAT, "-o", out_tmpl, url],
                timeout=420)
    if proc.returncode != 0:
        return None
    files = sorted(media_dir.glob("src_" + hid + ".*"))
    return files[0] if files else None


def probe(path: Path) -> tuple[float, int, int]:
    """(duration_sec, width, height); zeros if ffprobe cannot read it."""
    p = _run([FFPROBE, "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height",
              "-show_entries", "format=duration",
              "-of", "json", str(path)], timeout=90)
    if p.returncode != 0:
        return 0.0, 0, 0
    try:
        d = json.loads(p.stdout)
        st = (d.get("streams") or [{}])[0]
        return (float(d.get("format", {}).get("duration", 0.0)),
                int(st.get("width", 0)), int(st.get("height", 0)))
    except Exception:
        return 0.0, 0, 0


def clip_window(duration: float, want: float) -> tuple[float, float]:
    """Last `want` seconds -- the payoff lands at the end of a highlight cut."""
    want = min(want, MAX_CLIP_SEC)
    if duration <= want:
        return 0.0, round(max(duration, 0.1), 2)
    return round(duration - want, 2), round(want, 2)


def first_frame_yavg(src: Path, start: float) -> float | None:
    """Mean luma of the frame at `start`, via signalstats metadata."""
    p = _run([FFMPEG, "-hide_banner", "-ss", "%.2f" % start, "-i", str(src),
              "-frames:v", "1", "-vf", "signalstats,metadata=print",
              "-f", "null", "-"], timeout=90)
    m = re.search(r"lavfi\.signalstats\.YAVG=([\d.]+)", p.stderr or "")
    return float(m.group(1)) if m else None


def encode(src: Path, out: Path, start: float, length: float,
           fps: int, width: int, colors: int, hid: str) -> bool:
    # Per-clip palette filename: a shared palette.png races across clips.
    palette = out.parent / ("palette_" + hid + ".png")
    vf_gen = ("fps=%d,scale=%d:-1:flags=lanczos,"
              "palettegen=max_colors=%d:stats_mode=diff" % (fps, width, colors))
    p1 = _run([FFMPEG, "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-t", "%.2f" % length, "-i", str(src),
               "-vf", vf_gen, "-y", str(palette)], timeout=300)
    if p1.returncode != 0:
        return False
    lavfi = ("fps=%d,scale=%d:-1:flags=lanczos[x];"
             "[x][1:v]paletteuse=dither=bayer:bayer_scale=3" % (fps, width))
    p2 = _run([FFMPEG, "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-t", "%.2f" % length, "-i", str(src),
               "-i", str(palette), "-lavfi", lavfi, "-y", str(out)], timeout=300)
    palette.unlink(missing_ok=True)
    return p2.returncode == 0 and out.is_file()


def _start_rung(width: int, height: int) -> int:
    """Skip rungs that cannot plausibly fit the budget, using measured density.

    Deliberately backs off one rung from the estimate. The estimate is only
    accurate to ~30%, and erring high costs one wasted encode while erring low
    ships a needlessly ugly GIF -- actual measured size is what decides.
    """
    if not width or not height:
        return 0
    aspect = height / width
    budget_bytes = SIZE_BUDGET_MB * 1024 * 1024
    for i, (fps, w, colors, secs) in enumerate(LADDER):
        est = (w * (w * aspect)) * (fps * secs) * _density(colors)
        if est <= budget_bytes:
            return max(0, i - 1)
    return 0


def convert(highlight: dict, media_dir: Path) -> ClipResult:
    hid = str(highlight.get("id", "h?"))
    url = highlight.get("source_tweet_url", "")
    res = ClipResult(highlight_id=hid, source_url=url, ok=False)

    media_dir.mkdir(parents=True, exist_ok=True)
    print("\n  [%s] %s" % (hid, url))

    src = download(url, media_dir, hid)
    if not src:
        res.reason = "yt-dlp failed (private, deleted, or no video track)"
        print("       DROPPED -- " + res.reason)
        return res

    dur, w, h = probe(src)
    res.source_duration, res.source_dims = round(dur, 2), "%dx%d" % (w, h)
    if dur <= 0:
        res.reason = "ffprobe could not read duration"
        print("       DROPPED -- " + res.reason)
        src.unlink(missing_ok=True)
        return res
    print("       source: %dx%d, %.2fs" % (w, h, dur))

    out = media_dir / (hid + ".gif")
    start_idx = _start_rung(w, h)
    if start_idx:
        note = "skipped rungs 0-%d (est. over budget for %dx%d)" % (start_idx - 1, w, h)
        res.ladder_log.append(note)
        print("       " + note)

    for idx in range(start_idx, len(LADDER)):
        fps, width, colors, secs = LADDER[idx]
        start, length = clip_window(dur, secs)

        # Nudge off a black/transition first frame -- Outlook shows only frame one.
        nudges = 0
        if start > 0:
            while nudges < DARK_FRAME_MAX_NUDGES:
                yavg = first_frame_yavg(src, start)
                res.first_frame_yavg = yavg
                if yavg is None or yavg >= DARK_FRAME_YAVG:
                    break
                start = round(min(start + DARK_FRAME_NUDGE_SEC, max(dur - 0.5, 0.0)), 2)
                length = round(min(length, max(dur - start, 0.1)), 2)
                nudges += 1
            if nudges:
                res.ladder_log.append(
                    "first frame dark (YAVG<%.0f) -- nudged start +%.1fs"
                    % (DARK_FRAME_YAVG, nudges * DARK_FRAME_NUDGE_SEC))
        res.dark_frame_nudges = nudges

        if not encode(src, out, start, length, fps, width, colors, hid):
            res.ladder_log.append("fps%d w%d c%d %.1fs -> ENCODE FAILED"
                                  % (fps, width, colors, length))
            print("       fps%3d w%3d c%3d %4.1fs  ENCODE FAILED"
                  % (fps, width, colors, length))
            continue

        size_mb = out.stat().st_size / 1048576
        under = size_mb <= SIZE_BUDGET_MB
        res.ladder_log.append("fps%d w%d c%d %.1fs -> %.2fMB%s"
                              % (fps, width, colors, length, size_mb,
                                 "" if under else " (over)"))
        print("       fps%3d w%3d c%3d %4.1fs  %5.2fMB  %s"
              % (fps, width, colors, length, size_mb, "OK" if under else "over"))

        if under:
            res.ok = True
            res.output_file = out.name
            res.clip_start, res.clip_len = start, length
            res.final_fps, res.final_width, res.final_colors = fps, width, colors
            res.size_mb = round(size_mb, 2)
            res.step_downs = idx - start_idx
            src.unlink(missing_ok=True)
            return res

    # Ladder exhausted. Drop rather than ship: a 6 MB GIF on cellular is its own
    # kind of broken, and the spec's rule is that wrong is worse than nothing.
    if out.is_file():
        size_mb = out.stat().st_size / 1048576
        res.size_mb = round(size_mb, 2)
        res.reason = ("could not reach %.1fMB budget (floor %.2fMB)"
                      % (SIZE_BUDGET_MB, size_mb))
        out.unlink(missing_ok=True)
    else:
        res.reason = "all ladder rungs failed to encode"
    res.step_downs = len(LADDER) - start_idx
    print("       DROPPED -- " + res.reason)
    src.unlink(missing_ok=True)
    return res


def convert_plan(highlights: list, media_dir: Path) -> tuple[list, list]:
    """Convert every highlight; return (surviving highlights, all results)."""
    ok, results = [], []
    for hl in highlights:
        r = convert(hl, media_dir)
        results.append(r)
        if r.ok:
            ok.append({**hl, "gif_file": r.output_file, "size_mb": r.size_mb})
    return ok, results


def cleanup_old_gifs(media_dir: Path, history: list, days: int = 30) -> int:
    """Delete GIF files older than `days`. History records are never deleted --
    they feed the periodic editorial review agent. Defined, not scheduled."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    removed = 0
    for rec in history:
        try:
            if date.fromisoformat(rec.get("date", "")) >= cutoff:
                continue
        except ValueError:
            continue
        name = rec.get("output_file", "")
        f = media_dir / name
        if name and f.is_file():
            f.unlink()
            removed += 1
    return removed
