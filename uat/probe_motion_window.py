"""
THROWAWAY PROBE — does motion detection find the actual play?

The pipeline currently clips the LAST 6 seconds of every source video, on the
theory that "the payoff lands at the end." That theory produced a standings
graphic instead of a Tatis catch (the McAfee clip is 98s; its last 6s is an
outro card). This probe tests the alternative: scan each source for the
highest-MOTION 6-second window and clip THAT instead.

For every test clip it:
  1. downloads the source (reusing an earlier download if present),
  2. measures per-frame motion via ffmpeg scdet mafd (mean absolute frame
     difference — literally how much moved between frames),
  3. computes where the peak-motion 6s window sits vs where "last 6s" sits,
  4. prints a per-second motion sparkline so you can SEE where the action is,
  5. extracts BOTH windows as GIFs named <id>_LAST.gif and <id>_MOTION.gif.

Then open uat/probe/ and eyeball: on the 3 real plays, does _MOTION land on the
action while _LAST misses? On the 3 studio/talk clips, is the motion flat the
whole way through (evidence they should be REJECTED, not re-windowed)?

Run:  python uat/probe_motion_window.py
Paste the terminal output back, and tell me which _MOTION.gif files landed on
the real moment.

No API keys needed — this is just yt-dlp + ffmpeg. Nothing here writes to the
pipeline; all output goes to uat/probe/.
"""

from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path

# Reuse the pipeline's own binary discovery + encode so the extracted GIFs match
# production encoding exactly (fair comparison). Same-dir import.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from highlight_to_gif import (
        FFMPEG, FFPROBE, tools_available, probe, encode, clip_window,
        MAX_CLIP_SEC,
    )
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "Could not import from highlight_to_gif.py — run this from the repo so "
        "uat/highlight_to_gif.py is importable.  Error: %s" % e
    )

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROBE_DIR = SCRIPT_DIR / "probe"
PROBE_DIR.mkdir(parents=True, exist_ok=True)

# Fixed encode rung for the probe — content is what matters, not file size.
PROBE_FPS, PROBE_W, PROBE_COLORS = 12, 400, 128

# --------------------------------------------------------------------------
# Test set: 3 genuine plays + 3 studio/talk clips.
#
# The plays SHOULD show a clear motion spike that _MOTION lands on and _LAST
# misses. The studio clips SHOULD show flat motion throughout — which is the
# argument for rejecting them by duration/motion rather than trying to
# re-window them. URLs pulled from the 08-19 run log and the 08-21
# highlight_plan.json.
# --------------------------------------------------------------------------
CLIPS = [
    # id                url                                                            what it should be
    ("play_ll_walkoff", "https://twitter.com/SportsCenter/status/2090244505668563067", "LL walk-off home run (31.5s)"),
    ("play_ll_catch",   "https://twitter.com/espn/status/2090190670778478733",         "LL diving catch, portrait (10.0s)"),
    ("play_wnba",       "https://twitter.com/espn/status/2089869381404704969",         "WNBA Mitchell/Mabrey confrontation (43.1s)"),
    ("talk_mcafee",     "https://twitter.com/PatMcAfeeShow/status/2090140380528361672", "McAfee segment — the 98s 'Tatis' culprit"),
    ("talk_espn_mvp",   "https://twitter.com/SportsCenter/status/2089838219642769543",  "Rogers/Passan MVP studio discussion"),
    ("talk_pmt_dino",   "https://twitter.com/BarstoolBigCat/status/2090132520259965139", "PMT PCA dinosaurs talk"),
]

SPARK = " ▁▂▃▄▅▆▇█"


def _run(cmd: list, timeout: int = 420) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def download_reuse(url: str, hid: str) -> Path | None:
    """Download once; reuse on re-runs so you're not re-fetching every time."""
    existing = sorted(PROBE_DIR.glob("src_" + hid + ".*"))
    if existing:
        print("       (reusing existing download)")
        return existing[0]
    url = url.split("#")[0]
    out_tmpl = str(PROBE_DIR / ("src_" + hid + ".%(ext)s"))
    proc = _run([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings",
                 "--no-playlist",
                 "-f", "best[width<=480]/best[height<=480]/best[height<=720]/best",
                 "-o", out_tmpl, url])
    if proc.returncode != 0:
        return None
    files = sorted(PROBE_DIR.glob("src_" + hid + ".*"))
    return files[0] if files else None


def motion_profile(src: Path, hid: str) -> list[tuple[float, float]]:
    """Return [(pts_time, mafd), ...] — per-frame mean absolute frame difference.

    mafd is ffmpeg scdet's motion measure: how much the frame changed from the
    previous one. High during action, near-zero on a static graphic or a
    talking head. This is the signal we're testing as a window selector.

    IMPORTANT (Windows): scores are read from ffmpeg's STDERR via
    metadata=print, NOT written with metadata=print:file=<path>. A Windows path
    carries a drive-letter colon (C:\\...) which ffmpeg's filtergraph parser
    reads as an option separator, breaking the whole filter ("Error opening
    output files: Invalid argument"). Printing to the log avoids the path
    entirely. Note: NO -loglevel error here — that would suppress the very
    metadata lines we need to read.
    """
    p = _run([FFMPEG, "-hide_banner", "-i", str(src),
              "-an", "-vf", "scdet=s=0,metadata=print",
              "-f", "null", "-"])
    text = p.stderr or ""
    if "scd.mafd" not in text:
        print("       ⚠ scdet produced no motion metadata.")
        last = (text.strip().splitlines() or ["(no stderr)"])[-1]
        print("         last log line: " + last)
        return []

    prof: list[tuple[float, float]] = []
    cur_t: float | None = None
    for line in text.splitlines():
        mt = re.search(r"pts_time:([\d.]+)", line)
        if mt:
            cur_t = float(mt.group(1))
            continue
        mm = re.search(r"lavfi\.scd\.mafd=([\d.]+)", line)
        if mm and cur_t is not None:
            prof.append((cur_t, float(mm.group(1))))
    return prof


def peak_motion_window(prof: list[tuple[float, float]], dur: float,
                       want: float = MAX_CLIP_SEC) -> tuple[float, float, float]:
    """Slide a `want`-second window in 0.5s steps; return (start, len, score) of
    the window with the greatest summed motion."""
    want = min(want, dur)
    if dur <= want or not prof:
        return 0.0, round(min(dur, want), 2), sum(m for _, m in prof)
    best_start, best_score = 0.0, -1.0
    step, start = 0.5, 0.0
    last_start = max(dur - want, 0.0)
    while start <= last_start + 1e-9:
        s = sum(m for t, m in prof if start <= t < start + want)
        if s > best_score:
            best_score, best_start = s, start
        start = round(start + step, 2)
    return round(best_start, 2), round(want, 2), best_score


def sparkline(prof: list[tuple[float, float]], dur: float, buckets: int = 40) -> str:
    """One char per time-bucket, height = mean motion in that bucket."""
    if not prof or dur <= 0:
        return ""
    sums = [0.0] * buckets
    cnts = [0] * buckets
    for t, m in prof:
        b = min(int(t / dur * buckets), buckets - 1)
        sums[b] += m
        cnts[b] += 1
    means = [(sums[i] / cnts[i]) if cnts[i] else 0.0 for i in range(buckets)]
    hi = max(means) or 1.0
    return "".join(SPARK[min(int(v / hi * (len(SPARK) - 1)), len(SPARK) - 1)]
                   for v in means)


def fmt_window(start: float, length: float) -> str:
    return "[%5.1fs -> %5.1fs]" % (start, start + length)


def main() -> None:
    ok, msg = tools_available()
    print("tooling:", msg)
    if not ok:
        raise SystemExit("Install the missing tools and re-run.")

    print("\n" + "=" * 70)
    print("MOTION-WINDOW PROBE  —  peak-motion 6s  vs  last 6s")
    print("=" * 70)

    rows = []
    for hid, url, note in CLIPS:
        print("\n[%s] %s" % (hid, note))
        print("   ", url)

        src = download_reuse(url, hid)
        if not src:
            print("       DROPPED — yt-dlp could not fetch (private/deleted?)")
            rows.append((hid, note, None))
            continue

        dur, w, h = probe(src)
        if dur <= 0:
            print("       DROPPED — ffprobe could not read duration")
            rows.append((hid, note, None))
            continue
        print("       source: %dx%d, %.2fs" % (w, h, dur))

        prof = motion_profile(src, hid)
        if not prof:
            rows.append((hid, note, None))
            continue

        spark = sparkline(prof, dur)
        m_start, m_len, m_score = peak_motion_window(prof, dur)
        l_start, l_len = clip_window(dur, MAX_CLIP_SEC)
        l_score = sum(m for t, m in prof if l_start <= t < l_start + l_len)

        print("       motion : |%s|  (0s -> %.0fs)" % (spark, dur))
        print("       LAST 6s : %s  motion sum %8.1f" % (fmt_window(l_start, l_len), l_score))
        print("       PEAK 6s : %s  motion sum %8.1f" % (fmt_window(m_start, m_len), m_score))
        gap = "same window" if abs(m_start - l_start) < 0.6 else \
              "PEAK is %.1fs earlier than LAST" % (l_start - m_start)
        print("       verdict : %s" % gap)

        # Extract both windows so you can watch what each rule actually grabs.
        last_gif = PROBE_DIR / (hid + "_LAST.gif")
        motion_gif = PROBE_DIR / (hid + "_MOTION.gif")
        okl = encode(src, last_gif, l_start, l_len, PROBE_FPS, PROBE_W, PROBE_COLORS, hid + "L")
        okm = encode(src, motion_gif, m_start, m_len, PROBE_FPS, PROBE_W, PROBE_COLORS, hid + "M")
        print("       gifs    : %s  |  %s"
              % ("LAST ok" if okl else "LAST FAILED",
                 "MOTION ok" if okm else "MOTION FAILED"))

        rows.append((hid, note, (dur, l_start, m_start, l_score, m_score)))

    # ---- summary ----------------------------------------------------------
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("%-16s %6s  %-9s %-9s  %s" % ("id", "dur", "last@", "peak@", "note"))
    for hid, note, data in rows:
        if not data:
            print("%-16s %6s  %-9s %-9s  %s" % (hid, "—", "—", "—", note + "  [DROPPED]"))
            continue
        dur, l_start, m_start, l_score, m_score = data
        print("%-16s %5.1fs  %6.1fs   %6.1fs    %s"
              % (hid, dur, l_start, m_start, note))

    print("\nOpen:  %s" % PROBE_DIR)
    print("Compare each  <id>_LAST.gif  vs  <id>_MOTION.gif.")
    print("The question: on the 3 play clips, does _MOTION land on the moment")
    print("that _LAST misses? On the 3 talk clips, is motion flat throughout")
    print("(i.e. no window is right — they should be rejected, not re-windowed)?")


if __name__ == "__main__":
    main()
