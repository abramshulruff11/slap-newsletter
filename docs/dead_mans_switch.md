# Dead man's switch (SLA-56)

Every other check on this pipeline runs **inside** the GitHub Actions job, so none of them can
report a job that never started or was killed partway through. The dead man's switch is the one
alert that does not depend on the job: the job **pings** an external service, and the **service**
emails Abram when the pings don't arrive. The email comes from a different sender but lands in
the same inbox, with a subject like `DOWN | SLAP daily newsletter`.

Service: **healthchecks.io** (free tier). This was option 1 in the ticket, and it is the only one
that still works during a full GitHub Actions outage. A second workflow in this repo would share
Actions' queueing delays and outages with the job it is supposed to watch.

## What gets pinged, and what each outcome means

| Job behaviour | Pings | What the service does |
|---|---|---|
| Normal run, daily email sent (whatever the verdict) | `start` → success | Nothing |
| Run broke, but the daily email went out and says so | `start` → success | Nothing. The email already reported it. A second alert for the same failure is noise |
| Run reached the end but the **daily email did not send** | `start` → `fail` | Alerts **immediately**. The heartbeat is the only channel left |
| Workflow **never started** (dropped schedule, Actions outage, workflow disabled) | none | Alerts at the **cutoff** |
| Job started and was **killed** (timeout, runner lost) | `start` only | Alerts at the latest 12h after the start ping. The finish step is `if: always()`, so on a cancel GitHub usually still runs it, and that sends `fail` right away |

`heartbeat.py` always exits 0 and does nothing until the `SLAP_HEARTBEAT_URL` secret exists, so
monitoring can never break the newsletter.

## The cutoff: 12 hours after the cron = 18:17 UTC

The daily cron is `17 6 * * *` UTC. Measured delays between the cron time and when the job actually fired:

| Date | Fired (UTC) | Delay |
|---|---|---|
| three consecutive days (early Sept) | 11:20–11:24 | +5h05m |
| 2026-09-18 | 11:22 | +5h05m |
| 2026-09-19 | 11:06 | +4h49m |
| 2026-09-20 | 11:30 | +5h13m |
| 2026-09-21 | 12:55 | **+6h38m** (worst) |

The latest a normal run can finish is the worst start plus the job's 30-minute `timeout-minutes`,
so **13:25 UTC**. A cutoff at **18:17 UTC** leaves **4h52m** of headroom beyond that. That covers
another delay about 70% as large as the worst one on record, stacked on top of it. Every run in
the table would have finished at least 4¾ hours before the alert, so none of them would have
been a false alarm.

In local time that is **2:17 PM EDT / 1:17 PM EST**. The service schedule is in UTC to match
GitHub, which ignores DST.

**Why not earlier?** An earlier cutoff buys little. On a day the morning run never happens, the
12:30 PM ET publish job already sends its "Substack publish needs attention" email, because it
finds no handoff for today. That alert is the early signal, as long as Actions itself is working.
This switch is the backstop for the day Actions isn't working, and on that day the goal is to
never cry wolf rather than to win back an hour. If a week of real runs shows the delay settling
well below 6h, the grace time can be tightened in the service's UI with no code change.

## One-time setup (Abram)

1. Sign up at <https://healthchecks.io> with the Gmail address the daily email goes to.
2. **Add Check**:
   - Name: `SLAP daily newsletter`
   - Schedule: **Cron**, expression `17 6 * * *`, time zone **UTC**
   - Grace time: **12 hours**
3. Under **Integrations**, leave the default email integration pointed at that Gmail address.
4. Copy the check's ping URL (`https://hc-ping.com/<uuid>`).
5. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**, name
   `SLAP_HEARTBEAT_URL`, value = that URL.

The next scheduled run arms it. Its log will show `✓ heartbeat: sent 'start'` and later
`✓ heartbeat: sent 'success'`, and the check turns green on healthchecks.io.

## Pausing it on purpose

When the workflow is disabled on purpose (vacation, a planned break), open the check on
healthchecks.io and press **Pause**. A paused check sends no alerts. It goes back to
monitoring by itself on the next ping, so turning the workflow back on is all it takes to
re-arm it. There is nothing to undo.

**Don't** silence it by deleting the secret. That doesn't stop the service, which would then
see no pings and alert every day.

## Still open after this change

- **The zero-false-alarm target has not been measured yet.** It can only be measured against
  real runs once the check is armed. Watch the check's event log for the first week: every day
  should show a `start` followed by a success, with no alert emails.
- A day the morning run never happens can produce **two** emails: the noon publish job's "needs
  attention" email and, later, this one. They come from different systems and they back each
  other up, which is the point.
