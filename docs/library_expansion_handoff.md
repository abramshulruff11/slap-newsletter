# GIF + Meme Library Expansion — Handoff to a Local Session

**Branch:** `claude/expand-meme-gif-library-wb0j4h` (7 commits ahead of `main`, 0 behind as of 2026-09-16)
**Written by:** a Claude Code **cloud** session, for a Claude Code session running **on Abram's machine**.

---

## Why this document exists

The cloud session cannot reach Giphy or Imgflip. Both are blocked by the
container's egress policy — `curl`, `urllib` and WebFetch all get a 403 from
the proxy, confirmed repeatedly. That is not a missing API key: `api.imgflip.com/get_memes`
needs no auth at all and is still blocked.

A session on Abram's machine has network and a real `.env`. So the work splits:

| Step | Needs network to Giphy/Imgflip? | Who |
|---|---|---|
| Discover new GIF/meme candidates | **yes** | local |
| Judge fit, write library entries | no | either |
| Render thumbnails / worked examples for review | **yes** | local |
| Verify meme panel ORDER | **yes** | local |
| Apply review verdicts, run tests | no | either |

**If you are the local session, the network-bound steps are yours.** Do not
conclude a probe script is broken because a cloud session said it could not run
it — re-read this table first.

---

## State of the two libraries

### GIF library — `prompts/gif_library.DRAFT.json` — DONE for now
223 entries, 174 verified / 49 retired, **0 candidates**, 20 categories.
Abram reviewed the whole thing on 9/15 via the review page; verdicts are applied.

Thinnest categories (selectable entries): `betrayal_self_sabotage=3`,
`domination=4`, `denial_copium=6`, `debate_takes=7`. If another expansion round
happens, start there.

### Meme library — `prompts/meme_library.DRAFT.json` — REVIEW IN FLIGHT
30 templates, all still `candidate` **in this branch**. Template ids and
`box_count` were verified against Imgflip's live API on 2026-08-27; panel ORDER
by render probe on 2026-09-01. Zero `role_unverified` / `box_count_unverified`
flags remain. **Do not redo that work.** What has never been reviewed is the
*semantics*: whether box purposes, the valence rule and the examples describe a
joke that lands.

---

## Outstanding work

1. **Abram ran the meme review locally and the result was never pushed.** As of
   this writing the branch shows no commit applying it, so
   `prompts/meme_library.DRAFT.json` and `prompts/meme_selector_index.txt` may
   be modified in his working tree. **Check `git status` first.** If they are
   dirty, that is the review — commit and push it rather than regenerating
   anything:
   ```bash
   git add prompts/meme_library.DRAFT.json prompts/meme_selector_index.txt
   git commit -m "Apply meme semantic review"
   ```

2. **The meme expansion round has not started.** Nobody has run
   `meme_library_expand_probe.py` yet. See the workflow below.

---

## Tooling map — check here before building anything

A previous session almost rebuilt `review_gifs.py` from scratch because it did
not look first. Everything below already exists.

### GIF
| File | What it does | Network |
|---|---|---|
| `gif_library_expand_probe.py` | Giphy search API → prints new candidate ids | **yes** (`GIPHY_API_KEY`) |
| `uat/review_gifs.py` | library → `uat/gif_review.html`, Verify/Retire/Export | optional |
| `uat/apply_gif_decisions.py` | `gif_decisions.json` → writes statuses back | no |

`review_gifs.py --no-api` renders keyless `giphy.com/embed` iframes instead of
resolving thumbnails. Prefer it unless you need real thumbnails: the default
path costs **one API call per entry** (~223), and that is what exhausted the
daily Giphy quota on 2026-08-26 and blanked a UAT run's GIFs.

### Meme
| File | What it does | Network |
|---|---|---|
| `meme_library_expand_probe.py` | Imgflip `get_memes` → templates not yet in `CURATED_TEMPLATES`, with authoritative `box_count` | **yes** (no key needed) |
| `uat/review_memes.py` | library → `uat/meme_review.html`, Verify/Retire/Export | optional |
| `uat/apply_meme_decisions.py` | `meme_decisions.json` → statuses + regenerates the selector index | no |
| `uat/probe_meme_box_order.py` | renders marker captions to verify panel ORDER | **yes** (`IMGFLIP_*`) |

`review_memes.py --with-renders` captions each template with its own worked
example so you can see the joke rather than infer it. Imgflip's `caption_image`
is free and URLs are cached in `uat/meme_review_cache.json`.

All generated outputs (`*_review.html`, `*_decisions.json`, caches, `*.backup-*.json`)
are gitignored — they rebuild from the tracked library files.

---

## Adding new meme templates — the full workflow

1. **Probe** (local): `python -X utf8 meme_library_expand_probe.py > probe_candidates.txt`
   Commit that file so any session can read it without it passing through chat.
2. **Choose + author** (either): pick templates that fit SLAP's comedic engines,
   then write the full entry — box semantics, valence, `use_when` /
   `do_not_use_when`, worked + anti example, `selector_line`, tags.
3. **Wire three files in lockstep** — `CURATED_TEMPLATES` in `generate_memes.py`,
   the library JSON, and regenerate `prompts/meme_selector_index.txt`
   (`meme_library.write_selector_index()`). `uat/tests/test_meme_library.py`
   fails if any of the three disagree, so this is self-checking.
4. **Verify panel ORDER** (local): `python -X utf8 uat/probe_meme_box_order.py`,
   then open `prompts/meme_box_order_review.html`.

**Step 4 is not optional.** `box_count` from Imgflip is authoritative; panel
*order* is not, and the library has no inert tier to catch a mistake — see below.

---

## Hard-won facts. Re-learning any of these costs a day.

- **The meme library has no safety net.** `meme_library.py` never read `status`
  at all, so `candidate` gated nothing and every template was live regardless.
  A new template ships the moment it is in the library and in
  `CURATED_TEMPLATES`. This is how `vince-mcmahon-reaction` shipped a blank
  payoff frame for weeks. Retirement was made real on this branch:
  `active_templates()` drops retired templates from `build_selector_index()`,
  `valid_slugs()` and `engine_alternatives()`. `get_template()` stays permissive
  on purpose so an already-planned slug still resolves.

- **The two library files are formatted differently.** `gif_library.DRAFT.json`
  uses compact leaf arrays (`"tags": ["a", "b"]`); `meme_library.DRAFT.json` is
  plain `json.dumps(indent=2)`. Verified both ways round-trip byte-for-byte.
  `library_json.dumps_matching_style(obj, original_text)` at the repo root
  matches whichever the file already uses — **always** write libraries through
  it. Writing the wrong one reflows the meme file by 425 lines and the GIF file
  by ~2,175, burying the real edits. For a reviewed library the diff is the only
  record of what the pass decided.

- **Decision exports replay old localStorage.** The review pages accumulate
  verdicts in `localStorage` and never clear them, so an export contains every
  decision ever made on that browser, not just this session's. Abram's 9/15
  export had 209 decisions of which only **19** changed anything; the other 190
  were replays of earlier passes. Harmless to re-apply (idempotent), and in fact
  useful — those replays are what cleared the eyeball backlog — but never read
  the row count as "how much was reviewed just now". Check `new_status` against
  the library's *current* status to find the real changes.

- **`old_status` in an export can be stale** for the same reason. Trust the
  library, not the export's idea of the previous state.

- **A note that says nobody looked is not proof nobody looked.** 149 GIF entries
  sat at `status: verified` while their own note said "NOT yet eyeballed by
  Abram" — they had been reviewed, but the old applier wrote `status` and left
  the note alone. `apply_gif_decisions.py` now strips that marker from any entry
  it touches. All 209 are cleared; the count is 0.

- **`test_history_dedup.py` fails on `main`.** Verified against a clean
  `origin/main` worktree — `is_recently_used sees a same-run entry: expected
  True, got False`. Pre-existing, unrelated to this branch. 11 of the 12 offline
  suites pass. Do not "fix" it as part of library work.

- **`uat/warm_gif_cache.py` is broken** the same way `review_gifs.py` was: it
  does `sys.path.insert(0, str(UAT_DIR))` then imports `gif_url_cache`, which
  moved to the repo root in the 2026-09-01 consolidation. One-line fix, not yet
  applied. `uat/probe_source_res.py` and `uat/probe_motion_window.py` do the same
  path insert and may have the same latent problem.

- **Sourcing signal from the 9/15 review** (12 of 19 AI-sourced candidates kept):
  official sports-channel uploads went 7/7 — both UFC knockouts, Liverpool FC,
  all three ESPN Stephen A. Smith. Small or anonymous uploaders went ~1/5. And
  "a famous thing *titled* Sabotage will read as self-sabotage" went **0/4** —
  a music video does not carry the emotional beat. Weight future picks toward
  official channels with sports-native content.
