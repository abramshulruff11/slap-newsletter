# Task: Build the SLAP Meme Library (box-semantics layer)

## Context

SLAP's meme system currently works off `CURATED_TEMPLATES` in
`generate_memes.py` — a flat slug → Imgflip template ID dictionary, 30
entries, informally grouped by comment headers (Comparison/Preference,
Escalation/Levels, Denial/Copium, Reaction/Surprise,
Domination/Superiority, Betrayal/Self-Destruction, Resignation/Walking
Away, Debate/Takes). These IDs are battle-tested — several carry
`# FIXED: was X` comments documenting past slug-drift bugs that were
already found and corrected. **Do not touch, re-derive, or "improve" any
`template_id` value.** That part is solved and out of scope here.

What's missing, and what's causing bad output, is box semantics. The
writer currently has to guess what each caption box is "for" with no
guidance, and it's landing wrong — text in the wrong slot, wrong tone for
the box's role. Two templates already have hardcoded panel-expansion logic
in `generate_memes.py` (`_expand_boxes()`) because their physical panel
count doesn't match how many distinct captions a writer naturally writes:
- `gru-plan`: 4 physical panels, writer writes 3 captions, panel 4 repeats
  panel 3 verbatim (the repeat IS the joke — Gru staring at the flaw again)
- `anakin-padme`: writer writes 2 captions, panel 3 is blank, panel 4
  repeats panel 2 (Padme repeating her hopeful question)

`mocking-spongebob` also has hardcoded alternating-caps logic
(`_capitalize_boxes()`) that only applies to its mocking panel, not its
setup panel.

This task is to build a new file, `prompts/meme_library.DRAFT.json`, that
documents box semantics for all 30 existing templates so a future meme
selector (a small LLM call, not yet built) can write captions that
actually fit each box's role — the same way `prompts/gif_library.DRAFT.json`
documents `use_when`/`do_not_use_when` for GIFs. Read that file first for
the schema conventions this should mirror (status field meaning,
`_meta` block, category structure).

## Task

For each of the 30 slugs in `CURATED_TEMPLATES`, write an entry with:

```json
{
  "slug": "drake",
  "template_id": "181913649",
  "status": "candidate",
  "category": "comparison_preference",
  "pattern_type": "reject_approve",
  "box_count": 2,
  "boxes": [
    { "index": 0, "purpose": "the option being rejected/disapproved of" },
    { "index": 1, "purpose": "the option being approved/preferred" }
  ],
  "use_when": "...",
  "do_not_use_when": "...",
  "special_handling": null,
  "tags": ["comparison", "preference", "upgrade"],
  "note": "AI-authored box semantics, not yet reviewed by Abram."
}
```

Field notes:

- **`status`** here does NOT refer to the template_id (already verified) —
  it refers to whether the *box semantics/use-case metadata* has been
  human-reviewed. Every entry you write is `"candidate"`. There is no
  `"verified"` tier yet for this file; that only happens after Abram's
  review pass.
- **`category`** — use the existing comment-header groupings from
  `generate_memes.py` as your starting point (snake_case them), but you
  don't have to force-fit every template into its current group if a
  different grouping is genuinely more accurate. Flag any you move.
- **`pattern_type`** — this is the important new field. Identify the
  *shape* of how captions map onto panels. Don't force every template
  into one taxonomy — name what you actually observe. Expect things like:
  - narrative setup→payoff (two beats telling a story: e.g.
    `hide-the-pain-harold`, `this-is-fine`)
  - entity labeling (boxes label things/people in a static scene rather
    than tell a sequential story: e.g. `distracted-boyfriend`,
    `expanding-brain`)
  - reject→approve (a preference comparison: `drake`, `two-buttons`)
  - escalation sequence (ordered intensity levels: `expanding-brain`,
    `panik-kalm-panik`, `vince-mcmahon-reaction`)
  - repeat-for-emphasis (a caption or panel that deliberately repeats:
    `gru-plan`, `anakin-padme`)
  Use your judgment for templates that don't fit these cleanly — invent
  a new pattern_type name rather than force a bad fit, and flag it.
- **`boxes`** — one entry per *physical* panel (match `box_count` to what
  the template actually renders, not how many captions a writer writes —
  e.g. `gru-plan` is `box_count: 4` even though the writer only supplies
  3 distinct captions). Purpose should be specific enough that a caption
  writer knows what belongs there without seeing an example.
- **`special_handling`** — for `gru-plan`, `anakin-padme`, and
  `mocking-spongebob`, describe in plain language what
  `_expand_boxes()`/`_capitalize_boxes()` already does in code, so this
  file doesn't quietly contradict the actual runtime behavior. `null` for
  everything else.
- **`use_when` / `do_not_use_when`** — same spirit as the GIF library:
  what emotional/situational beat this template is for, and where it'd be
  a near-miss misuse. Base this on the template's real-world meaning, not
  just its category label.
- **`tags`** — a handful of lowercase tags per entry. Try to converge on
  a shared vocabulary across entries rather than inventing one-off tags
  per template (skim your own entries as you go and reuse terms).

## Scope note (different from the GIF expansion task)

This is **not** an open-ended search-and-expand job like the GIF library
task — the universe here is fixed and small (exactly the 30 existing
`CURATED_TEMPLATES` entries). Do all 30 in this pass. Do not add new
templates, do not query the Imgflip `get_memes` live endpoint, do not
invent new slugs. This task is purely: document semantics for what
already exists.

## Constraints

- Do not modify `generate_memes.py`.
- Do not modify `prompts/gif_library.DRAFT.json` or `gif_selector.DRAFT.py`.
- Do not build the meme selector itself — this file is metadata only,
  consumed by a selector that doesn't exist yet.
- Include a `_meta` block at the top of the new file (mirror
  `gif_library.DRAFT.json`'s `_meta` conventions) explaining: this file's
  purpose, that `template_id` values are inherited/trusted from
  `CURATED_TEMPLATES` and must stay in sync with it, that `status` here
  tracks semantic-review state only, and the `pattern_type` taxonomy is
  open/extensible rather than fixed.

## Deliverable

- New file: `prompts/meme_library.DRAFT.json`, all 30 templates covered.
- A written summary (in your final response) of:
  - Any templates where box semantics were genuinely ambiguous or you
    had low confidence in the purpose you assigned
  - Any templates you recategorized from their original comment-header
    grouping, and why
  - Any new `pattern_type` values you had to invent beyond the examples
    given above
  - Confirmation that `special_handling` for `gru-plan`, `anakin-padme`,
    and `mocking-spongebob` accurately reflects the current code in
    `generate_memes.py`
