# SLAP Newsletter — Claude Code Brief
# Date: March 24, 2026
# Context: Multi-pass newsletter generation pipeline

## PROJECT LOCATION
C:\Users\abram\OneDrive\Documents\Coding\slap-newsletter

## WHAT THIS PROJECT IS
SLAP (Sports Lunch Afternoon Post) is a daily sports newsletter. The generation pipeline uses Claude API to turn raw sports content (RSS headlines + tweets) into a newsletter.

## CURRENT ARCHITECTURE (just rebuilt today)
The pipeline runs in passes:
- Pass 1: Story Selector + Structural Outliner (prompts/pass1_story_selector.txt)
  - Takes raw_content.json, outputs newsletter_skeleton.json
  - Selects stories, assigns depth, assigns tweets to stories, creates block-by-block skeleton
- Pass 2: Writer (prompts/pass2_writer.txt)
  - Takes skeleton, writes commentary blocks in SLAP voice, outputs HTML
- Pass 3: GIF Finder (prompts/pass3_gif_finder.txt)
  - Takes GIF slots from skeleton, returns specific search terms
- Pass 5: Editor (prompts/editor_prompt.txt)
  - Rewrites for flow/cohesion

Key files:
- generate_newsletter.py — main pipeline script
- prompts/pass1_story_selector.txt — Pass 1 prompt (NEEDS UPDATES)
- prompts/pass2_writer.txt — Pass 2 prompt
- prompts/pass3_gif_finder.txt — Pass 3 prompt
- prompts/editor_prompt.txt — editor pass prompt
- prompts/voice_examples.txt — hand-written voice examples (DO NOT MODIFY)
- prompts/editorial_annotations.txt — editorial thinking guide
- prompts/rolling_feedback.txt — recent issue feedback
- prompts/base_prompt.txt — old single-pass prompt (retired, kept for reference)

## TASK: Update pass1_story_selector.txt

We just ran the first test of Pass 1. The skeleton output had these problems:

### PROBLEM 1: Only 3 headlines, target is 4-6
The selector chose: replacement refs (lead), Flacco signing (medium), Tokyo Toe (medium).
It missed: Tua signing with Falcons (prove-it narrative, great hook), Lavonte David retirement (14 seasons, Canton talk). Both had enough juice for at least a small headline.
FIX: Make the 4-6 headline target harder. Add language like: "If your plan has fewer than 4 headlines, go back through the raw content and look harder. A 3-headline issue almost always means you missed something. Tua signing a $1.3M prove-it deal IS a headline — the hook is 'league made its choice on him.' A respected veteran retiring after 14 seasons with Canton buzz IS a headline — even as a small/respectful one."

### PROBLEM 2: Source concentration — 4 Schefter tweets out of 11
The source_check correctly identified the problem (max_from_single_source: 4) but the selector didn't fix it before outputting.
FIX: Add an instruction that the source_check is not just a report — it's a GATE. If max_from_single_source > 3, the selector MUST go back and replace excess tweets from that account before outputting. Add: "The source_check fields are not just metrics — they are validation gates. If any gate fails, revise the plan before outputting. Do not output a plan that fails its own quality checks."

### PROBLEM 3: Lead story selection
Replacement refs was chosen as lead but it's a developing/speculative story with no funny tweets, no memes, just Schefter news. On the same day, Flacco at 41 has age jokes, Elite Dragon memes, absurd QB room age stats. The lead should be the story with the most SLAP juice, not the "biggest" news.
FIX: Reinforce that lead selection is about SLAP potential (best tweets, funniest angles, richest connections), not news importance. Add: "A developing story with only news-breaking tweets and no comedy/reaction content should NOT be the lead. The lead needs the best meme/joke/reaction material. A story with better tweets and more comedy angles should lead even if it's 'smaller' news."

### PROBLEM 4: Consecutive commentary violations not caught
Tokyo Toe story has blocks [commentary, commentary, gif, commentary, commentary] — that's two pairs of consecutive commentary blocks. The quality_check reported 0 violations.
FIX: Add explicit counting instructions: "To count consecutive_commentary_violations: walk through every story's blocks array in order. Any time you see two or more commentary blocks in a row without a tweet/gif/dash_list between them, that's a violation. Count each pair. If the count is > 0, you MUST restructure those blocks before outputting — insert a tweet or gif slot between consecutive commentary blocks."

### PROBLEM 5: Timeline framing sentences are too explanatory
Current: "AJ Dybantsa describing his love life in basketball terms is peak Gen Z"
Should be: "This kid is going through it" or no framing at all
FIX: Add: "Timeline framing sentences tell the reader HOW TO FEEL, not what the tweet says. The reader will see the tweet — don't summarize it. GOOD: 'This kid is going through it.' BAD: 'AJ Dybantsa describing his love life in basketball terms is peak Gen Z.' If the tweet is self-explanatory, set framing to null."

## HOW TO MAKE THE CHANGES
Open prompts/pass1_story_selector.txt and apply the five fixes above. Integrate them into the existing sections where they fit naturally — don't just append them at the bottom. The prompt should read as one cohesive document.

After updating, we'll rerun: python generate_newsletter.py --skeleton-only
