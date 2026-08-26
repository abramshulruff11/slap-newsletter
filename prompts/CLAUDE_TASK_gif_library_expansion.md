# Task: Expand the SLAP GIF Library (reaction-GIF candidates)

## Context

SLAP is a daily AI-generated sports newsletter. It's moving GIF selection
from blind live Giphy search (unreliable — random caption overlays, near
misses, staleness) to a curated library model, matching how
`generate_memes.py`'s `CURATED_TEMPLATES` already works for memes: verified
IDs, tagged by meaning/use-case, matched against the writer's described
moment by a small LLM selector instead of a raw text search.

The library lives at `prompts/gif_library.DRAFT.json`. It's still in DRAFT
status (not wired into the pipeline yet) and mixes two entry types:
- `"status": "verified"` — Abram has personally eyeballed these. Do not
  touch, reorder, or edit these entries.
- `"status": "candidate"` — AI-proposed, not yet reviewed. This is the
  bucket you're adding to.
- `"status": "retired"` — pulled by feedback, kept so it's never re-proposed.
  Do not remove these or reuse their ids.

Read `prompts/gif_library.DRAFT.json` in full before doing anything —
especially the `_meta` block, which explains the ID scheme, verification
states, and cooldown model. Do not skip this; the schema conventions
(especially `id` = bare Giphy ID, never a full URL) matter.

## Task

1. Run the probe script from the repo root:
   ```
   python gif_library_expand_probe.py
   ```
   This hits Giphy's real search API using each category's existing tags
   as search terms and prints new candidate GIFs (id, title, uploader,
   review URL) that aren't already in the library. It is read-only —
   it does not modify anything.

   If a category comes back thin (few or no good candidates from its
   existing tags), you may re-run narrowed to that category with your own
   better search terms:
   ```
   python gif_library_expand_probe.py --category CATEGORY_NAME
   ```
   (Adding a one-off `--query` override isn't built into the script yet —
   if you want that, editing the script's `search_terms` construction for
   a single run is fine and low-risk; it's a probe script, not production.)

2. For each category, from the printed candidates, judge fit using:
   - The category's `use_when` / `do_not_use_when` (already in the library)
   - The GIF's `title` and `tags` from the probe output
   - Prefer results with a real uploader/channel (official league accounts,
     named studios, established meme-page channels) over anonymous
     re-uploads when you have a choice — same logic Abram used manually:
     an official channel upload is less likely to be a mislabeled or
     captioned-over version of the "real" meme.

   You cannot visually confirm GIF content (no image rendering here) —
   that's expected. Every entry you add MUST be `"status": "candidate"`,
   never `"verified"`. Abram does the visual light-review pass afterward;
   your job is narrowing the field to genuinely plausible matches, not
   final approval.

3. Add good candidates directly into `prompts/gif_library.DRAFT.json`,
   following the exact existing schema per entry:
   ```json
   {
     "id": "<bare giphy id from the probe output>",
     "label": "<short human label, e.g. what the probe's title was>",
     "status": "candidate",
     "note": "AI-sourced <today's date> via gif_library_expand_probe.py. NOT yet eyeballed by Abram.",
     "tags": ["<2-4 tags matching the category's existing tag vocabulary>"]
   }
   ```
   - Add a `"cooldown_days"` override only if the GIF is an extremely
     famous/overused meme (mirror the existing Vince McMahon / MJ Crying
     examples, which got 14 and 10 respectively instead of the 7-day
     default) — don't add this field otherwise.
   - Reuse the category's EXISTING tag vocabulary where the fit is genuine;
     don't invent a sprawling new tag taxonomy per entry.
   - If a candidate's fit doesn't cleanly match the category's `use_when`
     but is clearly good for some OTHER emotional beat not yet in the
     library, do not force it into the wrong category — leave it out and
     flag it in your summary instead (see deliverable below).

4. **Cap volume per run: add at most 5–8 new candidate entries per
   category.** This is a "propose ~10, let Abram review" step, not a
   final harvest — don't try to exhaustively fill every category in one
   pass. Thin categories (currently: most of them besides `escalation`)
   are the priority; don't over-invest in `escalation`, which is already
   reasonably stocked.

5. Do not touch `_meta`, do not touch any `verified` or `retired` entry,
   do not remove or rename categories, do not invent new categories
   without flagging it clearly in your summary first.

## Deliverable

- Updated `prompts/gif_library.DRAFT.json` with new `candidate` entries.
- A short written summary (in your final response, not a new file) of:
  - How many candidates were added per category
  - Any categories that stayed thin because the probe/search genuinely
    didn't surface good options — so Abram knows where to seed real
    examples himself rather than assuming AI coverage exists
  - Any candidate you found that seemed clearly great but didn't fit an
    existing category, with a suggested new category name/description
  - Anything that seemed like a mislabeled/wrong-content risk worth a
    flagged note even though you added it (e.g. ambiguous title, unclear
    uploader)

Do not wire the library into `generate_newsletter.py` or touch
`gif_selector.DRAFT.py` — that's a separate, later step gated on Abram's
review of this batch.
