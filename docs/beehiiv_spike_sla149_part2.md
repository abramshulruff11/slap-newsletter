# SLA-149 Beehiiv spike — part 2 (sections 3-7, 9) and updated section 1

Written 2026-10-20 from the desktop session. Research only; no test post. Vendor pages
(beehiiv.com) were read directly; everything from third-party blogs is marked *unverified*.

## Section 1 update (auto-publish) — see the Linear comment for detail
- Create-post API is real: `title` + `body_content` (HTML) or `blocks`; `status` draft|confirmed;
  `scheduled_at`; `email_settings`; `recipients`. A draft cannot be scheduled, so a Beehiiv flow
  sends `confirmed` + `scheduled_at` directly (no separate noon publish job).
- Plan gating is unresolved: API reference says Max + Enterprise, pricing page lists
  Free/Lite/Pro/Enterprise and says Send API is Pro + Enterprise, an old help article says
  Enterprise beta. Cheapest plausible: Pro ~$95-109/mo at 1k subs.

## 3. Brand and subscriber trust
- Substack's real assets are its app, notes/recommendation network and "Substack" name. For a
  ~30-subscriber, email-driven list, none of that is doing work today. *Judgment, not sourced.*
- Beehiiv sends from the publication's own sending domain with guided SPF/DKIM/DMARC setup
  (guided DNS setup, per a contra.com launch note and Feather; *vendor-adjacent*).
- Substack: custom domain costs a one-time $50 and a CNAME; third parties say the From address
  stays @substack.com and that you configure authentication yourself. *Conflicting, unverified.*
- Readers see a differently styled email either way; no source measured trust impact.

## 4. Referral program
- Beehiiv: built in, paid plans only. Each subscriber gets a unique link; the publisher sets
  milestones (referral counts) and rewards; tracking is automatic, fulfilment is manual.
  Lite = 3 milestones, Pro = unlimited (pricing page).
- Substack: comparison sites say no native equivalent (SparkLoop bolt-on needs manual CSV
  imports). *Unverified; check Substack's current features before relying on it.*
- Fit: a group-chat-voice sports newsletter spreads by forwarding. Rewards like a stat pack or
  a shout-out are cheap. Biggest growth lever on this list.

## 5. Feature audit
- Custom HTML: the create-post API accepts `html` and `table` blocks and a raw HTML body, so
  box score tables can be sent as HTML. **Not verified** that tables survive Gmail/Outlook
  consistently; images stay the safe path. Test before dropping the PNG pipeline.
- Dynamic content, advanced segmentation, automations (welcome series, re-engagement), A/B
  subject-line tests, polls, web archive: all listed by Beehiiv; Beehiiv's own comparison page
  says Substack lacks dynamic content, segmentation and automations (*vendor claim*).
- Ad network: official changelog says Scale/Enterprise only; Boosts (recommendation
  payments) exist. Prices quoted by third parties unverified.
- Free tier: 2,500 subscribers, but no Send API, automations or referrals.

## 6. Migration
- Posts: official import tool, 5-10 min; same `/p/` URL pattern, so no manual redirects
  needed. Comments, categories, video/podcast posts do not migrate; code blocks flatten.
- Subscribers: CSV import. Open-rate history does NOT carry over; only Substack's "Activity"
  rating maps to a custom field. Original subscribe dates default to import date unless Support
  fixes them.
- Paid/comped subscribers need Stripe and manual handling; irrelevant today (free list).
- Safe parallel run: import a small segment, send the same issue to both for a week, compare.

## 7. Deliverability
- No independent measurement found. Sources agree list quality matters more than platform.
- A new sending domain starts with no reputation and needs warm-up; a ~30 person list is
  too small for the warm-up to matter much. Substack's shared-domain risk claim is *unverified*.

## 9. Ideas Substack can't do (ranked effort vs impact)
1. **Referral rewards** (low effort, high impact): "refer 3 friends, get the weekly power
   rankings" — fully automated links and counting.
2. **Auto-send with no workaround** (low effort, high impact): retire the proxy, the 30-minute
   poll and the noon job; one scheduled API call.
3. **Welcome series** (low effort, medium): 3 emails explaining the voice, the box scores,
   how to reply.
4. **Favorite-team segmentation** (medium effort, high impact): a signup question, then a
   short team-specific block via dynamic content.
5. **Editions by sport** (medium, medium): MLB-only or NFL-only sends from the same run.
6. **Polls / trivia in the issue** (low, low-medium): "Who wins tonight" poll, with last
   night's result in the next issue.
7. **Sponsor placements / ad network** (high effort, low today): not worth it at 30 readers.

## Recommendation (preliminary)
Stay on Substack for now; revisit when the plan question is answered. Beehiiv wins on
auto-posting reliability, referrals and segmentation, but the likely cost is $95-109/mo for a
free ~30-subscriber list, and Substack posting works today. Two things would flip it:
(a) support confirms create-post on a plan under ~$60/mo, or (b) Substack breaks again.
Next step if wanted: ask Beehiiv support which plan includes create-post and whether
`scheduled_at` also times the email send.
