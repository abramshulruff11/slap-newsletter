# GIF + Meme Library Expansion — Handoff to a Local Session

**Branch:** `claude/expand-meme-gif-library-wb0j4h` (7 commits ahead of `main`, 0 behind as of 2026-09-16)
**Updated:** 2026-09-15 by a local session, which ran the meme expansion round described below.
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

### Meme library — `prompts/meme_library.DRAFT.json` — EXPANDED 2026-09-15, REVIEW STILL OPEN
**44 templates across 21 engines**, all still `candidate`. Template ids and
`box_count` were verified against Imgflip's live API on 2026-08-27; panel ORDER
by render probe on 2026-09-01. Zero `role_unverified` / `box_count_unverified`
flags remain. **Do not redo that work.** What has never been reviewed is the
*semantics*: whether box purposes, the valence rule and the examples describe a
joke that lands.

**The 2026-09-15 round added 14 templates and 5 engines.** They were chosen by
comparing *demand against supply* rather than by picking popular templates:
`meme_history.json` holds 54 memes over 28 days (~1.9/day), and grouping those
by engine shows where rotation was starving. `emotional_whiplash` had been used
5 times against **one** template; `lopsided_exchange` 4 times against one;
`denial_amid_disaster` 7 times against two. With ~13 meme slots a week and a
7-day cooldown, a depth-1 engine gets repeated by construction — and
`swap_cooled_templates()` can only swap *within* an engine, so depth 1 means the
repeat is kept. Re-run that comparison before the next round; it is three lines
of `collections.Counter` over `meme_history.json` joined to each template's
`engine`.

Added: `pawn-stars-best-i-can-do` (lopsided_exchange 1→2),
`gus-fring-we-are-not-the-same` (hardened_vs_soft 2→3), `laughing-leo`
(contempt_face_off 2→3), `uno-draw-25-cards` (subject_abandons 3→4), `bike-fall`
(self_inflicted 3→4), `roll-safe-think-about-it` (obliviousness_gap 3→4), plus
five new engines: `simple_take_vindicated` (bell-curve), `mismatched_response`
(flex-tape, mother-ignoring-kid-drowning), `outside_looking_in`
(squidward-window, two-guys-on-a-bus), `truth_refused` (the-scroll-of-truth,
charlie-conspiracy) and `obligatory_ritual` (say-the-line-bart).

**Still depth-1 and still the two hungriest engines: `emotional_whiplash`
(panik-kalm-panik, 5 uses) and `forced_choice_dilemma` (two-buttons, 3 uses).**
Nothing in Imgflip's top-100 is an honest sibling for either — whiplash needs a
genuine three-beat worry/relief/worse-worry, and two-buttons needs two
*same-valence* options with the choice not yet made. Deliberately left alone
rather than filled with a bad fit, which is the failure mode `_meta.engines`
warns about. If you want them filled, the search has to go outside the top-100,
which means an id that `get_memes` cannot confirm — render it and count panels
before writing any semantics.

---

## Outstanding work

1. **The meme semantic review has still not been applied.** Checked on
   2026-09-15 from the local machine: the working tree was **clean** and all
   templates read `status: candidate`. So the earlier note that Abram's review
   might be sitting unpushed in his working tree is settled — there was nothing
   there to rescue. The review has to be done (or redone) from the page.

   `uat/meme_review.html` has been rebuilt with `--with-renders` and now covers
   all 44 templates, each captioned with its own worked example so the joke can
   be seen rather than inferred. Open it, mark Verify/Retire, Export Decisions,
   then:
   ```bash
   python -X utf8 uat/apply_meme_decisions.py
   ```
   Remember the export replays every decision ever made in that browser — check
   `new_status` against the library's current status to see what actually
   changed.

2. **The meme expansion round is DONE for this pass** (see above). The probe
   output is committed as `probe_candidates.txt` — 74 candidates, of which 14
   were taken. The 60 left over are still there with real ids and box counts, so
   the next round does not need to re-probe unless Imgflip's top-100 has moved.

3. **The GIF library was not touched in this pass.** It remains as described
   above: 223 entries, 0 candidates, reviewed 9/15. Thinnest categories are
   still `betrayal_self_sabotage=3`, `domination=4`, `denial_copium=6`,
   `debate_takes=7`.

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

- **Imgflip renders can be looked at, and should be.** A local session can
  caption any template with positional markers (`BOX 0`, `BOX 1`, ...), download
  the JPG and *read the image*. Every one of the 14 templates added on
  2026-09-15 had its panel order settled that way before a single box semantic
  was written, and two were not what a reasonable guess would have said:
  `flex-tape`'s index 2 is the man in the TOP panel (index 1 is the tape in the
  bottom one), and `mother-ignoring-kid-drowning`'s index 2 is the mother, not
  the drowning child. Both would have shipped inverted jokes. This is strictly
  better than `probe_meme_box_order.py`'s review page when you only need a
  handful, and it is why none of the new entries carries `role_unverified`.
  One gotcha: `i.imgflip.com` returns **403 to urllib's default User-Agent**.
  Send a browser UA (`requests.get(url, headers={"User-Agent": "Mozilla/5.0 ..."})`)
  or the download fails while the render itself succeeded.

- **Two suites fail on `main`, not one.** `test_history_dedup.py` is the known
  one. `test_run_quality.py` also fails, and did so on a detached checkout of
  `c54891e` with no working-tree changes — verified 2026-09-15. 10 of the 12
  offline suites pass. Neither is library work; do not fix them as part of it.

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
