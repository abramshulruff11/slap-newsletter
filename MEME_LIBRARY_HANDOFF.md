# Meme Library — Handoff

Context transfer from a Claude Code **cloud** session (no access to the local drive)
to a session that **does** have local drive access.

- **Branch:** `claude/meme-library-build-7s0n0r`
- **Head at handoff:** `e93b710`
- **Repo:** `abramshulruff11/slap-newsletter`

---

## 1. READ THIS FIRST — the branch and the local drive have diverged

The cloud session could only see what was pushed to GitHub. A UAT run on the local
machine printed console lines that **do not exist anywhere on the branch**:

    [meme-check] two-buttons  3/3 boxes  ok
    [gif-lib] fatigue_over_it -> Tired / over it (FOX TV)
    [tiers] Tier 1 library: 6   Tier 3 search: 1/3 cap
    data-tier3-reason="..."

None of `meme-check`, `gif-lib`, `tier3_reason` are on the branch. Meanwhile the
branch's wiring prints `[memelib] Pass 2 spec injected for: ...`, and that line did
**not** appear in the local run.

**Conclusion: the local `uat/` tree is a newer/different codebase than the branch,
and the meme-library wiring never ran. The UAT output reviewed so far is a baseline
of the OLD meme path, not a test of this work.**

### First task for the new session
1. `git fetch origin claude/meme-library-build-7s0n0r`
2. Diff the local `uat/` tree against the branch and find what is local-only
   (the gif-library tier system, the meme-check box validator, highlight tiers).
3. Reconcile — most likely: merge the branch's meme-library work INTO the local
   tree, since local appears strictly ahead on other features.
4. Commit the local-only work so both sides stop drifting.

Do NOT assume the branch is authoritative. It is not.

---

## 2. What the work is

Replaces mechanics-only meme metadata with a **comedic-engine** library so the
newsletter's memes stop reading as generic comparisons.

### The core diagnosis
The old `meme_reference.txt` told the writer *which panel is which* but never
*who the meme is about* or *where the laugh comes from*. Worst case, the original
v1 library had **drake's valence inverted** — it described box 1 as the smarter
option, when the joke requires box 1 to be the WORSE option the subject chose.
The example still in `meme_reference.txt`
(`"Watching the game on ESPN || Reading about it in SLAP"`) is a straight upgrade
with no subject and no bad decision — an ad, not a joke. That is the failure mode
this work targets.

### Schema (per template, 30 total, all `status: "candidate"`)
| field | meaning |
|---|---|
| `engine` | cluster key; shared comedic mechanism, defined once in `_meta.engines` |
| `subject` | `{required, who, placement, rule}` — placement is `box:N` or `copy` |
| `comedic_engine` | one sentence: where the laugh comes from |
| `valence` | REQUIRED relationship between boxes, phrased so it can be violated |
| `boxes[]` | per PHYSICAL panel: `{index, purpose, valence}` |
| `worked_example` / `anti_example` | shape demos — **never** reusable as fact |
| `selector_line` | one compact line, used to build the Pass 1 index |
| `special_handling` | only gru-plan, anakin-padme, mocking-spongebob |

`_meta.engines` holds 16 engines (10 real clusters covering 24 templates,
6 singletons). Each carries `summary`, `comedic_engine`, `valence_rule`,
`key_test`, `failure_mode`, `members`. `key_test` is the discriminator vs the
nearest neighbouring engine — e.g. drake vs two-buttons is decided entirely by
whether the choice is ALREADY MADE (drake) or still being sweated (two-buttons).

`template_id` values are inherited verbatim from `CURATED_TEMPLATES` in
`generate_memes.py` and must stay in sync. Nothing re-derives them.

### Consumption model (chosen deliberately — do not inject the whole file)
| stage | gets | ~tokens |
|---|---|---|
| Pass 1 | `prompts/meme_selector_index.txt`, compact, all 30, grouped by engine | ~1,950 |
| Pass 2 | full entries for ONLY the 1-2 templates Pass 1 chose | ~550 each |
| *(rejected)* | *whole library injected into Pass 2* | *~20,700* |

Rejected because 20K tokens of meme docs would crowd `voice_examples.txt` out of
the writer's attention. The Pass 2 spec goes in the **user message**, never the
cached system block — the selection changes per run and would thrash the cache.

---

## 3. Files on the branch

**New**
- `meme_library.py` (repo root) — access layer. `load_selector_index()`,
  `collect_meme_slugs(plan)`, `format_meme_specs(slugs)`. Shared by prod and UAT;
  prod does not import it yet. Degrades to no-op on any bad input.
- `prompts/meme_library.DRAFT.json` — the library, 30 entries + `_meta.engines`.
- `prompts/meme_selector_index.txt` — generated Pass 1 index. Do not hand-edit.
- `prompts/meme_library_review.html` — standalone review page. Opens on the full
  30; images load live from Imgflip in the browser.
- `uat/tests/test_meme_wiring_dryrun.py` — stubs the Anthropic client and asserts
  the wiring end to end. **No API key, no spend.** Run:
  `python uat/tests/test_meme_wiring_dryrun.py`
- `prompts/meme_library.SCHEMA_PROPOSAL.json` — superseded, kept for reference.

**Changed — defect fix, applied to BOTH prod and UAT (they are identical)**
- `prompts/meme_reference.txt`, `uat/prompts/meme_reference.txt`
  - `distracted-boyfriend` moved 2-panel → 3-panel (box 3 = SUBJECT, middle figure)
  - `two-buttons` moved 2-panel → 3-panel (box 3 = SUBJECT, sweating figure)
  - Both previously left their subject panel EMPTY in production.
  - No code change needed: `_expand_boxes()` passes these slugs through and
    `generate_meme()` sends as many boxes as it receives.

**Changed — new behavior, UAT ONLY (per CLAUDE.md's UAT-first rule)**
- `uat/generate_newsletter_uat.py` — imports `meme_library`; appends the selector
  index to Pass 1's system prompt; adds `meme_template` + `meme_subject` to the
  `submit_story_plan` schema; injects selected specs into Pass 2's user message.
- `uat/prompts/pass1_story_selector.txt` — read by ENGINE first; subject is a GATE
  (cannot name a subject → seed no meme, use a GIF instead).
- `uat/prompts/pass2_writer.txt` — valence and subject rules are HARD; spec wins
  over general guidance.

Production `generate_newsletter.py` and `generate_memes.py` are **untouched**.

---

## 4. Open items

1. **Branch/local divergence** — blocking, see §1.
2. **Drake valence, unconfirmed.** In the UAT output the Nuggets drake meme was
   judged wrong; the correct box 0 is `PAYING BRUCE BROWN AND KCP` (the road not
   taken) against box 1 `5 YEARS, $125M FOR CHRISTIAN BRAUN` (the self-defeating
   thing they did). The rendered HTML had already swapped placeholders for
   `i.imgflip.com` URLs, so the actual `data-boxes` were never seen.
   **Get the pre-render `data-boxes`.** Hypothesis to confirm: the writer put a
   thing Denver actually DID in box 0 too, narrating a sequence instead of posing
   a choice. If so, add to the `subject_abandons_sensible_for_funky` valence rule:
   *both boxes must be actions available to the same subject at the same decision
   point; box 0 is the road NOT taken, never a state, outcome, or context.*
3. **Box ORDER unverified** for `distracted-boyfriend` and `two-buttons`. The box
   ROLES are confirmed; whether Imgflip index 0/1/2 maps to the assumed physical
   positions is not. Both carry a `box_order_caveat` field. A single real render
   settles it — if captions land on the wrong figures, permute the indices.
4. **Audit the other 28 for hidden subject boxes.** Two templates assumed to be
   2-box turned out to be 3-box with a subject panel. Any template with a
   "reactor" figure may be the same. The local `[meme-check]` validator already
   knows real box counts — **use it as the source of truth** and reconcile the
   library against it.
5. **The subject rule is advisory, not enforced.** Three levels: (1) library states
   it — done; (2) rule reaches the model — done in UAT; (3) rule is verified —
   NOT done. Level 3 needs `data-subject` on the meme-placeholder div so
   `pre_edit()` (Pass 5, deterministic) can check the subject actually appears in
   the section text. That is a `generate_memes.py` change.
6. **Pruning.** All 30 were built as requested, to be cut by hand. Weakest sports
   fits flagged in `_meta`: `epic-handshake` (shared thing has no caption box),
   `is-this-a-pigeon` (needs a rare confidently-wrong public claim),
   `one-does-not-simply` (narrow assumed-easy/actually-hard gap).
7. **Promotion.** Wiring is UAT-only. Promoting means having prod's
   `generate_newsletter.py` import `meme_library` the same way.
8. **Unrelated flag seen in the run:** Pass 1 account cap violation
   `{'JayCuda': 3}`.

---

## 5. Decisions already made — do not relitigate

- Injection model **(b)**: Pass 1 picks the slug from a compact index; Pass 2 gets
  only the chosen entries. (a) whole-library injection was rejected as too
  expensive/inaccurate; (c) a separate selector pass was deferred as more
  machinery than needed.
- Keep all 30 for now; the owner prunes. The library is expected to grow.
- The subject requirement is **explicit and mandatory**, not a suggestion.
- The library is deliberately **NOT forked** into `uat/prompts/` — it is
  structured data, not voice-bearing prose, and a fork would reproduce the UAT
  drift problem already tracked in CLAUDE.md. Both runners read the one file.
- `engine` and `pattern_type` are kept as **separate axes**: engine = shared
  comedic mechanism, pattern_type = structural caption-to-panel mapping.
- Worked examples are kept despite being fabricated, flagged illustrative-only in
  `_meta.examples_are_illustrative`. They teach shape better than prose.

---

## 6. Quick verification commands

```bash
# wiring, no API key, no spend
python uat/tests/test_meme_wiring_dryrun.py

# library integrity + template_id sync with CURATED_TEMPLATES
python -c "
import json,re,meme_library as m
d=m.load_meme_library(); t=d['templates']
ids=dict(re.findall(r'\"([^\"]+)\":\s+\"(\d+)\"',
    open('generate_memes.py').read().split('CURATED_TEMPLATES = {')[1].split('}')[0]))
print(len(t),'templates |ids in sync:',all(ids[e['slug']]==e['template_id'] for e in t))
print('box_count==len(boxes):',all(e['box_count']==len(e['boxes']) for e in t))
print('subject boxes:',[e['slug'] for e in t if e['subject']['placement'].startswith('box:')])
"

# full UAT (costs ~$1.40, needs .env with ANTHROPIC_API_KEY)
python uat/run_uat.py --passes 1,2
```

Watch for `[memelib] Pass 2 spec injected for: ...`. **If that line is absent, the
library wiring did not run.**
