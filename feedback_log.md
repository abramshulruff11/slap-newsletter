# SLAP Feedback Log

Persistent capture for observations about SLAP issues that might become rules.
Append-only. Low-friction. Dump notes here as you spot them — don't wait for
a structured review session.

---

## How to use this file

**When you (Abram) spot something:** add a bullet under the most recent dated
heading, or start a new heading if it's a new logging session. No required
schema. Just write what you noticed.

**When you want to review** (weekly, or whenever the Active section feels thick):
open a Claude Desktop conversation with filesystem access to the repo and say:

> "Review my SLAP feedback log against the recent archive and propose rule updates."

Claude will follow the review ritual below.

---

## Review ritual (for Claude)

When the user asks to review this log, do the following:

1. **Read all Active entries** in this file (everything below the `## Active`
   heading, excluding Resolved).

2. **Read the last 7 days of archived drafts** from `archive/YYYY-MM-DD/newsletter_draft.html`.
   Also read `recent_output.json` (story log) and `gif_history.json` from the
   relevant archive folders if needed.

3. **Read the current `prompts/rolling_feedback.txt`** so you understand what
   rules already exist. Do not propose duplicates.

4. **For each Active entry, do three things:**
   - **Verify** the pattern actually appears in the archived drafts. If the user
     says "Ben Affleck used 3 times," confirm it by finding the GIFs. Don't
     trust the observation blindly — patterns get misremembered.
   - **Classify** the entry:
     - `one-off` — a single error (e.g. typo, wrong player name). Log for
       editor-pass test cases but do NOT propose as a rule.
     - `pattern` — appears in 2+ recent issues. Candidate for a new rule.
     - `structural` — reader/editorial feedback about section balance, sport
       mix, length, etc. Candidate for a Pass 1 (selector) rule.
     - `voice` — observation about prose style. Candidate for an update to
       `voice_examples.txt` or `pass2_5_voice.txt`, not necessarily a new rule.
     - `architectural` — requires code changes, not prompt rules (e.g. GIF
       dedup logic, where feedback file is loaded).
   - **Propose** concrete language for any `pattern` or `structural` entries.
     Match the existing rolling_feedback rule style (heading, why-it-fails,
     fix, example). Tag which pass should enforce it.

5. **Output a single response** with:
   - For each entry: classification + verification result + proposal (if applicable).
   - A summary of what to add to which file.
   - Do NOT edit `rolling_feedback.txt` directly. Propose only. The user
     manually integrates approved changes.

6. **Do not modify this file** during review. After the user approves and
   integrates proposals, the user moves resolved entries from `## Active` to
   `## Resolved` themselves (or asks you to do it explicitly).

---

## Format conventions

- Group entries by logging session, dated heading: `### YYYY-MM-DD — context`
- Each bullet is one observation. Be specific. Reference issue dates if known.
- Tag one-off errors with `[one-off]` so they're easy to skip during pattern hunts.
- When an entry is integrated into a rule, move it to `## Resolved` with a
  pointer: `→ became RULE X in rolling_feedback.txt`.

---

## Active

### 2026-05-19 — first review session (reviewed 5/16-5/19)

**GIFs / memes**
- Ben Affleck smoking GIF appeared in 3 of last 4 issues (5/16, 5/18, 5/19). Same core meme, different mood words ("defeated", "sad", "defeated acceptance"). The 7-day concept dedup missed it because `normalize_gif_concept` takes first 4 words and the 4th word varies. Either narrow window to 3 words or build a known-meme registry.
- "trade-offer meme" appeared twice in the same 5/16 issue. Need within-issue dedup, not just cross-issue.
- General observation: GIF quality is hit-or-miss. Need to audit what's actually being pulled vs the search terms.

**Story selection / repetition**
- SGA MVP got full coverage in BOTH 5/17 (lead) and 5/18 (full section). Story_log marks it as `is_new: false` on 5/18 but it still got a full section, not just an ATL mention.
- Aaron Rodgers signing covered in 5/17 (full section) and again in 5/19 (full section with very similar framing). The 5/19 angle (showed up to OTAs at 7am) is news, but the framing was a re-run.
- Need a rule: if `is_new: false` and `resolved: false`, default to ATL unless there's a genuinely new development worth a section.

**Section structure**
- Reader feedback: "split up the sections better." Stacking 3 NBA stories with no breaks. Need sport variety enforcement in Pass 1 — max 2 stories from same sport across lead + supporting.
- NFL Schedule section in 5/16 had a headline but no body. Just 5 SharpFootball tweet links and a meme. Sections must have written content, not just tweet dumps.
- Harden tangent in 5/18 Cavs story was disconnected. Sub-tangents inside main stories need to either tie back or be cut.

**Around the League**
- BallsackSports posted an unrelated political/non-sports complaint and it made it into ATL (date?). Tweet Filter Rule 1 (sports only) and Rule 2 (Barstool filter) didn't catch it. Pre-edit pass should add content classification for ATL tweets.
- Duplicate tweet in 5/16: same StatMuse URL (Randle/Gobert stat) used in main story AND in ATL.

**Voice / callouts**
- Big Cat call-outs appearing across consecutive days. Pattern of leaning on the same accounts for color even when they're not the most relevant. Account hard cap (Rule 1) is at the issue level but doesn't track cross-issue dependence on specific voices.

**Factual / one-offs**
- [one-off] Misnamed Jalen Duren as "Isiah Duren" in 5/18. Player name verification not enforced anywhere. The orphan paragraph at the end of rolling_feedback Rule 7 mentions verification but it's not gated by any pass.
- [one-off] SGA age inconsistency in 5/17: headline says "25-year-old", body says "27 years old". Internal-consistency check in editor pass would catch this.

**Architectural**
- `rolling_feedback.txt` is only loaded by Pass 2 (Writer). Rules that target story selection (Rule 5 evergreen) or editing (Rule 6 punching down) don't reach the passes that should enforce them. Needs to be split by pass or tagged per-rule.

---

## Resolved

_(Empty. Move entries here once integrated into `rolling_feedback.txt` or
relevant prompt/code files, with a note about where they landed.)_
