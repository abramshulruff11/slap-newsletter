# SLA-149 Beehiiv spike — Part 1 (sections 1, 2, 8)

Status: **research only, from web-search snippets.** The sandbox cannot fetch beehiiv.com or
developers.beehiiv.com (proxy returns 403 on CONNECT), so no page was read in full. Every claim
below is marked **[snippet]** (seen in a search result summary) or **[unverified]**. Before any
decision, someone with browser access should confirm the items in "To verify".

No Beehiiv credentials exist in the repo, workflows or this session, so no proof of concept was
attempted.

## 1. Auto-publish and scheduled send

- A Create Post endpoint exists: `POST https://api.beehiiv.com/v2/publications/{publicationId}/posts`,
  bearer-token auth. [snippet] (developers.beehiiv.com/api-reference/posts/create)
- Content is sent as `body_content` (HTML) or `blocks`; a title plus one of those is the minimum. [snippet]
- Scheduling: `status: "confirmed"` publishes immediately, or at `scheduled_at` if given. [snippet]
  A `draft` cannot be scheduled. [snippet]
- **Default changed:** from 2026-08-06 a call with no `status` defaults to `draft` rather than
  sending. Always pass `status` explicitly. [snippet]
- There is a `post.scheduled` webhook. [snippet title only]
- **Plan gating is the open question.** The current support article is reported to say the Send API
  and Create post endpoint are on **Max and Enterprise**; a September 2025 article said Enterprise
  only, beta, by request. The pricing summary says Launch includes "API Access (excluding Send
  API)". [snippet, conflicting] This matches CLAUDE.md's "enterprise-only" note only partly: it
  looks like Max ($96/mo) may now unlock it.
- No example found of anyone running an automated or AI-generated daily newsletter through Create
  Post. Zapier's beehiiv app has a "New Post Sent" trigger but no create-post action in what I saw. [snippet]

**Provisional verdict:** technically feasible (draft + scheduled send via a documented endpoint,
no cookies, no Cloudflare workaround implied), **gated behind a paid tier**. Go/no-go depends on
confirming that tier.

**Versus Substack today:** Substack needs a residential proxy, `curl_cffi`, session cookies and our
own noon cron because the schedule endpoint is gone. Beehiiv offers a documented key-based API
with native scheduling, at a price.

## 2. Reliability and integration

- Rate limit: 180 requests/minute per organization, 429 on excess, `RateLimit-*` headers, backoff
  advised. [snippet] One third-party page says per-publication; trust the official wording.
- Webhooks exist for subscription and post events; official event list not seen. Scale tier and up
  is reported to include webhooks. [snippet]
- API is `/v2`. No changelog or deprecation policy found; the Aug 2026 default flip shows breaking
  behaviour changes do ship. [unverified beyond that]
- Idempotency: nothing found. A retried Create Post could make a duplicate, so we would reuse the
  existing handoff-file pattern (store the post id, check before creating).
- Bot blocking: an official key-based API should not hit the Cloudflare class of problem. [unverified]

## 8. Pricing

| Plan | Price | Subscribers | Notes [snippet] |
|---|---|---|---|
| Launch | free | 2,500 | API access excluding Send API |
| Scale | $43/mo (annual) | up to 100,000 | automations, webhooks, ad network, 0% take rate |
| Max | $96/mo | up to 100,000 | branding removal, priority support; Send API reportedly here |
| Enterprise | custom | 100,000+ | |

Prices differ between sources ($49 vs $43 Scale, $99 vs $96 Max); the official page was not read.
SLAP has ~30 subscribers, so the cap is irrelevant; **the cost is the tier needed for the Send
API**, roughly $96/mo if Max, versus $0 on Substack. Against the current ~$2-5/month pipeline
cost that is a large step.

## To verify (needs browser access)

1. Which plan actually includes Create Post / Send API today (Max vs Enterprise), and whether
   there is a trial or per-month billing.
2. `scheduled_at` format and timezone handling; whether `body_content` accepts full email HTML,
   including `<table>` markup and images.
3. Any limits on image size (the repo's `probe_quality_matrix.py` assumes 5 MB) and on posts per day.
4. Idempotency, error codes, official webhook event list, changelog.

## Sources

- https://www.beehiiv.com/support/article/36759164012439-using-the-send-api-and-create-post-endpoint
- https://www.beehiiv.com/support/article/29286794539671-how-to-access-the-beehiiv-send-api
- https://developers.beehiiv.com/api-reference/posts/create
- https://developers.beehiiv.com/welcome/rate-limiting
- https://developers.beehiiv.com/webhooks/post/scheduled
- https://www.beehiiv.com/pricing
- https://www.beehiiv.com/support/article/37992518472215-sending-beehiiv-posts-to-an-external-cms
