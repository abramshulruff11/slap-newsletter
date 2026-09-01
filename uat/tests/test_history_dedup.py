"""
Run:  python -X utf8 uat/tests/test_history_dedup.py

Locks two properties of the GIF and meme history writers that were in tension,
and where fixing one naively breaks the other.

Both writers kept an in-memory `history` list, appended each new entry to it so
that same-run dedup could see it, and then saved `new_entries + history` — a
list that already contained new_entries. Every entry was persisted TWICE.

Measured on the live files before the fix: meme_history.json held 60 rows and
30 distinct entries; gif_history.json the same. Both cap at 60, so each file
retained half the history it claimed to. For memes that still spanned 16 days.
For GIFs it spanned SIX — against a seven-day rotation lookback in
is_recently_used() and format_recent_media_block(). The rotation rule was
silently under-enforced: the oldest day in the window was evicted before it
could be read.

The naive fix — deleting the in-run insert — silently breaks same-run dedup
instead, letting one issue use the same GIF or meme template twice. So both
properties are asserted here together.

No API calls, no network.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import runner_common as R  # noqa: E402
import generate_memes as M  # noqa: E402


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 66)
print("HISTORY WRITERS — no double-write, dedup still sees the current run")
print("=" * 66)

# --- The save contract both writers share ---------------------------------
# save_*_history(repo_root, new_entries, stored_history) must write exactly
# new_entries + what was on disk. Passing a history that already contains
# new_entries is what produced the duplication.
for name, save, load, fname in (
    ("gif", R.save_gif_history, R.load_gif_history, "gif_history.json"),
    ("meme", M.save_meme_history, M.load_meme_history, "meme_history.json"),
):
    root = Path(tempfile.mkdtemp())
    stored = [{"date": "2026-08-30", "slug": "old", "url": "u-old",
               "search_term": "old"}]
    (root / fname).write_text(json.dumps(stored), encoding="utf-8")

    fresh = [{"date": "2026-09-02", "slug": "new", "url": "u-new",
              "search_term": "new"}]
    save(root, fresh, load(root))
    rows = json.loads((root / fname).read_text(encoding="utf-8"))
    check(f"{name}: rows written", len(rows), 2)
    check(f"{name}: newest first", rows[0].get("url"), "u-new")
    check(f"{name}: no duplicate rows",
          len(rows), len({json.dumps(r, sort_keys=True) for r in rows}))

# --- Same-run dedup must still see entries added during this run ----------
# The in-run insert stays; only the SAVE stops re-counting it.
print()
run_history = [{"date": "2026-09-02", "url": "u-1", "search_term": "slow clap"}]
check("is_recently_used sees a same-run entry",
      R.is_recently_used("u-1", run_history, days=7), True)
check("is_recently_used ignores an unrelated url",
      R.is_recently_used("u-2", run_history, days=7), False)
check("is_concept_recently_used sees a same-run concept",
      R.is_concept_recently_used("slow clap", run_history, days=7), True)

meme_history = [{"date": "2026-09-02", "slug": "distracted-boyfriend",
                 "boxes": ["A", "B", "C"]}]
check("is_template_recently_used sees a same-run template",
      M.is_template_recently_used("distracted-boyfriend", meme_history), True)
check("is_template_recently_used ignores an unused template",
      M.is_template_recently_used("expanding-brain", meme_history), False)

# --- The 60-row cap must now hold 60 REAL entries -------------------------
print()
root = Path(tempfile.mkdtemp())
stored = [{"date": "2026-08-01", "url": f"u{i}", "search_term": f"s{i}"}
          for i in range(59)]
(root / "gif_history.json").write_text(json.dumps(stored), encoding="utf-8")
R.save_gif_history(root, [{"date": "2026-09-02", "url": "u-new",
                           "search_term": "new"}], R.load_gif_history(root))
rows = json.loads((root / "gif_history.json").read_text(encoding="utf-8"))
check("cap retains 60 rows", len(rows), 60)
check("all 60 are distinct (was 30 before the fix)",
      len({r["url"] for r in rows}), 60)

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
