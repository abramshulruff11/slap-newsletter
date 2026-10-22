# SLA-131 spike — GIF sourcing options and the business question

Run 2026-10-13 from Abram's machine (live Giphy API, beta-key budget: 54 calls).
Read-only: nothing in the library or pipeline changed. Raw responses and the
thumbnail contact sheets used for the quality read were kept in the session
scratchpad, not committed.

---

## Part 1 — Does better raw material exist?

### 1. How candidates are sourced today

Every path is plain keyword search on Giphy's `/v1/gifs/search`:

| Path | Query | Where |
|---|---|---|
| Library build / expansion probe | the category's **own existing tags**, first 4 alphabetically, 10 results each | `gif_library_expand_probe.py` |
| 9/14–9/15 cloud rounds (20 candidates) | web search for famous GIFs (the API was unreachable from the cloud) | commits `5de5a35`, `00268ec` |
| Runtime fallback when the library has no fit | the writer's free-text term, top 5 | `runner_common.fetch_giphy_candidates()` |

The probe's design is part of the precision problem: it searches with the
vocabulary the category already has, so it can only find more of the same mood.

### 2. Giphy Related Search Terms (`/v1/tags/related/{term}`)

**A useful vocabulary expander for sports phrases. Harmful on bare mood words.**

| Seed | Related terms returned | Read |
|---|---|---|
| `walk off` | peter alonso, bat flip, bat toss, walkoff, wade davis, asdrubal cabrera | excellent: sport-native and situation-specific |
| `celebratory` | jump for joy, keep winning, emotional win, victory dance, big wins | decent: leans toward situations |
| `smug` | smirk, self satisfied, chuffed, told ya, humble brag | good synonyms |
| `copium` | joever, sadge, dont give me hope, gigachad, rhamondre stevenson | internet-native and on-tone |
| `domination` | dominatrix, kink, submissive, sadomasochism | **unsafe** |
| `blowout` | hair dryer, blow dry, hair extensions | wrong sense entirely |
| `respect` | dapl, plants, sunset, community | noise |

It answers the ticket's example only partly. "celebratory" does **not** surface
walkoff, clutch or blowout. You get there only by seeding a sport phrase.
Autocomplete (`/v1/gifs/search/tags`) is weaker still: mostly pop-culture
completions ("mind blown kramer", "sad cat").

Verdict: it can suggest search terms, but only with a filter in front. Never
feed its output straight into a search: one bad hop and the pool is BDSM content.

### 3. Tenor — not testable, and no longer an option

Google closed Tenor API sign-ups on 2026-01-13 and **decommissioned the API on
2026-06-30**. An unauthenticated call returns 403 today. The successor most apps
moved to (WhatsApp, Lovable, Minnit) is **Klipy**, an ad-funded GIF API. It was
not tested because it needs an account. Nothing found suggests it offers
situational structure beyond keyword search.

### 4. Giphy Categories and official channels

**Categories (`/v1/gifs/categories`): no help.** A fixed, dated tree. Under
`sports` there is only a list of leagues (mlb, nba, nfl…); under `emotions` and
`reactions`, generic feelings (angry, bored, mic-drop). Coarser than SLAP's
20 categories, not finer.

**Official channels, via `@channel query` search: the one real improvement,
with sharp limits.**

Pools of 15 compared side by side and judged by eye from thumbnail sheets
("usable" = would pass the 9/15 review for that situation):

| Situation | Today's style (mood word) | Sport term | Channel-scoped |
|---|---|---|---|
| Walk-off win | `celebration`: ~3/15. New Year's, confetti stickers, Biden | `walkoff`: 15/15 baseball, ~9/15 show the actual walk-off pile-up | `@mlb walk off`: 15/15 official, ~12/15 celebrations, **but 10 of 15 are the same Brewers game** |
| Lopsided win | `domination`: ~1/15. Five sexual/BDSM results, cartoons | `dominant`: 0/15, worse | `@ufc knockout`: 15/15 official, only ~3/15 read as a knockout. The rest are walkouts, interviews, belts, sponsor overlays |
| Elimination / dejection | `sad`: 0/15 sports (cats, Stitch, Michael Scott) | `heartbroken`: 0/15 sports | `@nba sad`: only 8 results, ~5 strong (Curry, the Harden/Embiid bench, Spike Lee) |
| Awe at a performance | `mind blown`: classic memes, ~6/15 | — | `@nba wow`: 15/15 official, **~11/15 strong reaction faces**. Best pool in the test |
| Spin / denial | `denial im fine`: ~4/15, mostly text stickers | — | `@nfl not worried`: **0 results** |
| Hot-take TV | — | — | `@espn first take`: ~6/11 Stephen A. Four are blank title cards |

What this shows:

- **Within a sport, the channel plus a sport-native term is a large precision
  jump.** It holds the 9/15 finding: official content is real, clean, uncaptioned
  and on-topic.
- **"Official" does not mean "reaction-ready".** The UFC pool is highlight
  footage, not a reaction. The 7/7 from 9/15 held only where the channel posts
  crowd and bench *faces* (NBA, ESPN studio). It did not hold for raw game action.
- **The NFL has no official Giphy channel.** No `nfl` account exists, and
  `@nfl …` returns zero. Football, SLAP's biggest sport, gets no help here.
  Team channels exist (Cowboys, Browns, Titans) but were not tested.
- **Non-sports moods get nothing.** Denial, smugness, financial pain and fatigue
  live in TV and movie clips, where plain keyword search stays the only source.
- **Channel pools cluster.** One game can fill a page, so diversity has to be
  enforced after search.

### 5. Labels: no endpoint supplies better ones

This matters most for the planned build. Across the 244 GIFs returned, **only 23
(9%) carried any `alt_text`**. Titles are auto-generated tag mash-ups: ten
different walk-off clips are all titled "Milwaukee Brewers Baseball GIF by MLB",
and UFC knockouts read "Mixed Martial Arts Sport GIF by UFC". Neither Giphy nor
any surviving alternative gives you a "what is happening in this GIF" label.

### Part 1 bottom line

- **Beats today's keyword search:** **channel-scoped search with a sport-native
  term** (`@nba wow`, `@mlb walkoff`, `@espn first take`), for sports-situation
  subcategories in MLB, the NBA, the NHL, UFC and ESPN studio shows. Related Terms
  is a good *source of those terms* only when its output is filtered.
- **Does not:** Tenor (gone), Categories (coarser than ours), Autocomplete,
  and channels for the NFL or for any non-sports mood.
- **Implication for the taxonomy build:** better *raw material* exists for the
  sports half of the subcategories. Better *labels* do not exist anywhere. The
  decided direction stands as planned: Claude tags from the frames, not from
  Giphy's metadata. One practical point: the beta key is capped at 100 calls an
  hour, so a candidate sweep across ~200 subcategories spreads over several hours
  or needs a production key.

---

## Part 2 — Is this a business?

### Landscape

| Who | What | Gap vs. the SLAP design |
|---|---|---|
| **Giphy** (Shutterstock, bought from Meta for $53M in 2023) | Largest library, keyword search plus a related-tags graph trained on search and share behaviour. Shutterstock said it would add AI and metadata. No shipped situational search was found. | Labels are auto-generated filler (see above). No mood, intensity or context axes. Entities only as uploader channels. |
| **Tenor** (Google) | API shut down 2026-06-30 | Google exited. A signal about how valuable a general GIF API is. |
| **Klipy** (2022, SF, ~$1.3M raised) | The ad-funded Tenor replacement: WhatsApp, Lovable, Minnit | A keyword-search plumbing play, monetised by ads |
| **Innagiffy** | "AI engine for reaction media": semantic search, mood-aware ranking, chat plugins, an API | **Pre-launch, founders unnamed, seed "planning"**. A thesis page, not a product. Closest in intent; plans a licensed corpus, not Giphy |
| **GifWingman** and similar | Consumer tools that read a chat screenshot and suggest a GIF reply | Retrieval for a conversation, not a tagged library. No entity layer |
| **WSC Sports** (and peers) | B2B AI that tags every moment of a broadcast (player, play type, crowd reaction) for leagues and broadcasters, 250–300 clips a game | The real holder of *sports entity plus situation* tagging. Sells to rights-holders, not to writers |
| Open-source / research | CLIP- or embedding-based GIF search demos (Pinecone tutorial, GiffySearch, thesis work) | The technique is commodity. The labelled corpus is the hard part |

### Is the gap real?

Yes, narrowly. Nobody sells **mood + intensity + situation + entity** retrieval
over sports reaction GIFs. Consumer GIF search is keyword-plus-popularity, and
the sports-specific tagging that does exist (WSC) sits upstream with the leagues.

### Who would want it?

The plausible buyers are sports newsletter writers, team and fan-site social
managers, and Slack/Discord sports communities. They exist, but they are small,
price-sensitive, and already served "well enough" by Giphy's search box. The
general-purpose version (any mood, any topic) is the market Google just walked
away from and Klipy is funding with ads. That is a weak signal for paid demand.

### Blockers (flag only, not legal analysis)

1. **Giphy's API Terms forbid exactly this product.** Verbatim: "You shall not
   use content you obtain through Giphy's products and services to create a
   database, directory, or index containing GIFs or digital stickers or to
   improve, edit, augment or supplement any existing database, directory, or
   index containing GIFs." It also bans mixing Giphy results with another
   provider's, and Giphy's docs forbid caching or proxying media without
   approval. A re-served tagged index of Giphy content is ruled out without a
   Giphy partnership.
2. **The underlying clips belong to the leagues.** The best raw material found
   (official MLB, NBA, UFC and ESPN channels) is league and broadcaster
   intellectual property, displayed on Giphy under Giphy's terms. Re-serving it
   commercially means licensing from the leagues, which is WSC Sports'
   business, not a side project's.
3. **Worth knowing for SLAP itself:** the curated library is, literally, an
   index of GIFs built partly from API results, so the clause above arguably
   reaches it too. In practice, SLAP is a free newsletter embedding Giphy's own
   URLs with Giphy attribution available, which is the normal use. But if SLAP is
   ever monetised, this belongs on the same re-check list as Lahman and the MLB
   Stats API.

### Gut check

**A byproduct, not a side-track.** The tagging is valuable because it makes SLAP
better, and SLAP's moat is the editorial judgment, not the index. As a
standalone product it hits a hard blocker (Giphy's terms), a rights wall on the
best content (the leagues), and a market where Google left and the survivor is
ad-funded. The one version worth a second look later is not "a GIF API" but
**the tagging method itself**: a Claude-driven situational tagger applied to a
corpus someone already has the rights to, such as a team's or league's own clip
library. That points at WSC-style buyers, so it is a different business from
the one in the ticket. Don't pursue it now.

---

Sources: [Tenor shutdown, HN](https://news.ycombinator.com/item?id=46603473) ·
[Tenor FAQ](https://support.google.com/tenor/answer/10455265?hl=en) ·
[GIPHY API Terms](https://support.giphy.com/hc/en-us/articles/360028134111-GIPHY-API-Terms-of-Service) ·
[GIPHY developer docs](https://developers.giphy.com/docs/api/) ·
[Shutterstock acquires GIPHY](https://alternativeto.net/news/2023/5/shutterstock-acquires-giphy-from-meta-for-53-million-following-uk-regulator-order-to-sell) ·
[Klipy / CB Insights](https://www.cbinsights.com/company/klipy) ·
[WhatsApp adds Klipy](https://betanews.com/article/whatsapp-is-adding-klipy-as-another-gif-provider/) ·
[Innagiffy](https://innagiffy.com/) ·
[GifWingman](https://www.gifwingman.com/) ·
[WSC Sports](https://wsc-sports.com/blog/industry-insights/create-once-publish-everywhere-ai-powered-sports-content-at-the-scale-and-speed-fans-expect/) ·
[Pinecone GIF search](https://www.pinecone.io/learn/gif-search/md/)
