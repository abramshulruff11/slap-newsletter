"""
SLAP Newsletter — the ONE daily email (SLA-52).

WHAT THIS SENDS
    A single message that is both the product and the status report:

      1. a PIPELINE STATUS panel — succeeded / partially succeeded / failed,
         every stage with its outcome, the actual error text of anything that
         broke, what the run-quality gate found, and whether the Substack draft
         exists for the 12:30 PM ET publish
      2. the daily COST breakdown per pass
      3. a copy-from-here marker
      4. the newsletter itself, with the per-sport box score images embedded
         INLINE (via cid:) under the "Box Scores" header — so one select-all →
         copy → paste carries the whole issue, images included

    IT ALWAYS SENDS. Before SLA-52 this step ran in the middle of the workflow,
    so a failure anywhere above it meant no email at all — the one case where
    Abram most needed to hear something was the case that stayed silent. It now
    runs last, under `if: always()`, and a run that produced no newsletter still
    produces an email saying which stage died and why.

THE 12:30 PM ET FOLLOW-UP
    `--publish-alert` sends a short second message from publish-substack.yml,
    and ONLY when the publish needs attention. A normal publish sends nothing:
    silence at 12:30 means it went out. (Decided 2026-09-19 — the morning email
    must not wait for noon, and a routine "published!" note is noise.)

    python email_newsletter.py                       # the daily status email
    python email_newsletter.py --publish-alert ...   # noon, on trouble only
"""

import argparse
import html as _html
import json
import os
import re
import smtplib
import subprocess
import sys
from pathlib import Path
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_status        # noqa: E402 -- records whether delivery actually happened
import pipeline_status   # noqa: E402 -- per-stage outcomes, read back for the panel

SCRIPT_DIR      = Path(__file__).resolve().parent
SUBSTACK_PATH   = SCRIPT_DIR / "newsletter_substack.html"
BOX_SCORE_DIR   = SCRIPT_DIR / "box_score"
COST_SUMMARY_PATH = SCRIPT_DIR / "cost_summary.json"
SUBSTACK_STATE_PATH = SCRIPT_DIR / "substack_post_state.json"
PUBLISH_RESULT_PATH = SCRIPT_DIR / "publish_result.json"

ET = ZoneInfo("America/New_York")

# Gmail rejects messages over 25 MB (and MIME base64 inflates binary by ~37%).
# Below this RAW total we embed the box scores inline (cid:) — self-contained,
# single copy/paste. Above it, we fall back to GitHub-hosted <img> URLs so the
# email stays tiny no matter how huge the slate (NFL Sundays, CFB Saturdays,
# March Madness). 15 MB raw ≈ 20.5 MB encoded — comfortably under the limit.
MAX_INLINE_RAW_BYTES = 15 * 1024 * 1024

_IMG_STYLE = "display:block;width:100%;max-width:680px;height:auto;margin:16px auto;"

_MONO = "'Courier New',Courier,monospace"
_LEVEL = {
    "success": ("✅", "#1a7f37", "#e9f6ec", "SUCCESS"),
    "partial": ("⚠️", "#9a6700", "#fff6e0", "PARTIAL"),
    "failed":  ("❌", "#b42318", "#fdeceb", "FAILED"),
}


# ---------------------------------------------------------------------------
# the status panel
# ---------------------------------------------------------------------------

def _substack_draft_note() -> tuple[str, bool]:
    """(sentence, ok) about today's Substack draft, read from the handoff file
    the morning --draft run writes. Keyed by ET date, the same way
    publish.py's publish_existing() decides whether the handoff is stale —
    a draft from a previous day is not a draft for today."""
    today = datetime.now(ET).date().isoformat()

    # A run that finishes after 12:30 ET publishes immediately rather than
    # waiting for tomorrow, so on those days "publishes at 12:30 PM ET" would
    # be a lie by the time this email is built. publish.py leaves its outcome
    # behind; prefer it over the handoff when it is today's.
    try:
        result = json.loads(PUBLISH_RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = {}
    if result.get("date") == today and result.get("outcome") == "published":
        return ("this run finished past 12:30 PM ET, so the issue was published "
                "immediately rather than held for tomorrow", True)

    try:
        state = json.loads(SUBSTACK_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ("no draft handoff was written — nothing will publish at "
                "12:30 PM ET", False)
    if state.get("date") != today:
        return (f"the handoff still names {state.get('date') or 'an unknown date'}"
                f" — today's draft was never created, so nothing will publish "
                f"at 12:30 PM ET", False)
    draft_id = state.get("draft_id")
    url = state.get("url")
    where = f' — <a href="{_html.escape(str(url))}">open it</a>' if url else ""
    return (f"draft <code>{_html.escape(str(draft_id))}</code> is queued and "
            f"publishes at 12:30 PM ET{where}", True)


def _stage_table_html(status: dict) -> str:
    rows = ""
    for r in pipeline_status.stage_rows(status):
        state = r["state"]
        if state == "ok":
            mark, colour = "✓", "#1a7f37"
        elif state == "failed":
            mark, colour = "✗", "#b42318"
        else:
            mark, colour = "–", "#8c8c8c"
        secs = f"{r['seconds']:.0f}s" if r.get("seconds") is not None else ""
        detail = ""
        if state == "failed":
            detail = f"exit {r.get('exit_code', '?')}"
            if not r.get("critical"):
                detail += " · non-blocking"
        elif state == "skipped":
            detail = "never ran"
        rows += (
            '<tr>'
            f'<td style="padding:2px 8px 2px 0;color:{colour};font-weight:bold;">{mark}</td>'
            f'<td style="padding:2px 12px 2px 0;color:#1a1a1a;">{_html.escape(r["name"])}</td>'
            f'<td style="padding:2px 12px 2px 0;color:#5f5f5f;text-align:right;">{secs}</td>'
            f'<td style="padding:2px 0;color:{colour};">{detail}</td>'
            '</tr>'
        )
    return ('<table cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;font-family:{_MONO};font-size:12px;'
            'margin:8px 0 0 0;">' + rows + '</table>')


def _broke_html(status: dict) -> str:
    """What broke, at which stage, and the real error text — the tail of that
    stage's own log. A report that only says "failed" sends you to the Actions
    tab, which is the trip this ticket exists to remove."""
    failed = pipeline_status.failures(status)
    if not failed:
        return ""
    out = ('<div style="margin:14px 0 0 0;font-weight:bold;color:#b42318;">'
           'WHAT BROKE</div>')
    for r in failed:
        note = r.get("note") or ""
        kind = "blocking" if r.get("critical") else "non-blocking"
        out += (f'<div style="margin:8px 0 0 0;color:#1a1a1a;">'
                f'<b>{_html.escape(r["name"])}</b> — exit {r.get("exit_code", "?")}'
                f' <span style="color:#8c8c8c;">({kind})</span>'
                + (f'<br><span style="color:#5f5f5f;">{_html.escape(note)}</span>'
                   if note else "")
                + '</div>')
        tail = r.get("log_tail")
        if tail:
            out += ('<pre style="margin:4px 0 0 0;padding:8px;background:#fff;'
                    'border:1px solid #e0c0bd;overflow-x:auto;white-space:pre-wrap;'
                    'word-break:break-word;font-size:11px;color:#5a1e19;">'
                    + _html.escape(tail) + '</pre>')
    return out


def _retries_html(status: dict) -> str:
    """Transient API failures the run rode out (SLA-54).

    Informational, and deliberately NOT part of the verdict: the whole point of
    a retry is that the run survived it. But a day that recovered from three
    rate limits and a day that sailed through look identical otherwise, and the
    first one is worth knowing about before it becomes the second kind of day."""
    retries = status.get("api_retries") or []
    if not retries:
        return ""
    out = ('<div style="margin:14px 0 0 0;font-weight:bold;color:#1a1a1a;">'
           'API RETRIES</div>')
    for r in retries:
        gave_up = "gave up" in str(r)
        colour = "#b42318" if gave_up else "#5f5f5f"
        mark = "✗" if gave_up else "↻"
        out += (f'<div style="color:{colour};margin-top:2px;">{mark} '
                f'{_html.escape(str(r))}</div>')
    return out


def _quality_html(status: dict) -> str:
    q = status.get("quality") or {}
    if not q:
        return ""
    c = q.get("counts") or {}
    line = " · ".join(filter(None, [
        f"{c.get('tweets', 0)} tweets",
        f"{c.get('gifs', 0)} GIFs",
        f"{c.get('memes', 0)} memes",
        f"{q.get('box_images', 0)} box score images",
        f"{q.get('media_share', 0)}% media share",
    ]))
    out = ('<div style="margin:14px 0 0 0;font-weight:bold;color:#1a1a1a;">'
           'RUN QUALITY</div>'
           f'<div style="color:#5f5f5f;margin-top:2px;">{line}</div>')
    for e in q.get("errors") or []:
        out += (f'<div style="color:#b42318;margin-top:2px;">✗ '
                f'{_html.escape(str(e))}</div>')
    for w in q.get("warnings") or []:
        out += (f'<div style="color:#9a6700;margin-top:2px;">⚠ '
                f'{_html.escape(str(w))}</div>')
    return out


def _status_block_html(status: dict, level: str, headline: str) -> str:
    icon, colour, bg, word = _LEVEL[level]
    today = datetime.now(ET).strftime("%Y-%m-%d")
    draft_note, draft_ok = _substack_draft_note()
    passes = status.get("incomplete_passes") or []

    body = (
        f'<div style="font-size:14px;font-weight:bold;color:{colour};">'
        f'{icon} PIPELINE {word} &mdash; {today}</div>'
        f'<div style="color:#1a1a1a;margin-top:2px;">{_html.escape(headline)}</div>'
        + _stage_table_html(status)
    )
    if passes:
        body += ('<div style="margin:10px 0 0 0;color:#b42318;">'
                 '✗ a pass returned incomplete output: '
                 + _html.escape(", ".join(str(p) for p in passes)) + '</div>')
    body += _broke_html(status)
    body += _retries_html(status)
    body += _quality_html(status)
    body += ('<div style="margin:14px 0 0 0;font-weight:bold;color:#1a1a1a;">'
             'SUBSTACK</div>'
             f'<div style="color:{"#5f5f5f" if draft_ok else "#b42318"};'
             f'margin-top:2px;">{draft_note}</div>'
             '<div style="color:#8c8c8c;margin-top:2px;">'
             'You only hear from the noon publish job if something goes wrong.'
             '</div>')

    return (f'<div style="font-family:{_MONO};font-size:12px;background:{bg};'
            f'border:1px solid {colour};padding:12px 14px;margin:0 0 8px 0;'
            'max-width:600px;">' + body + '</div>')


# ---------------------------------------------------------------------------
# the cost table (unchanged content, marker split out)
# ---------------------------------------------------------------------------

def _pricing_block_html():
    """Render the daily cost summary as a small HTML table.

    Returns '' if cost_summary.json is missing or unreadable. Visually distinct
    from the newsletter (mono font, dashed border, gray background) so Abram can
    skip past it during copy/paste into Substack."""
    if not COST_SUMMARY_PATH.exists():
        return ""
    try:
        data = json.loads(COST_SUMMARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""

    passes = data.get("passes", [])
    total  = data.get("total", 0.0)
    cost_date = data.get("date", "")

    rows = ""
    for p in passes:
        label = p.get("label", "?")
        model = p.get("model", "").replace("claude-", "")
        cost  = p.get("cost", 0.0)
        in_t  = p.get("in_tokens", 0)
        out_t = p.get("out_tokens", 0)
        rows += (
            '<tr>'
            f'<td style="padding:2px 10px 2px 0;color:#1a1a1a;">{label}</td>'
            f'<td style="padding:2px 10px 2px 0;color:#5f5f5f;">{model}</td>'
            f'<td style="padding:2px 10px 2px 0;color:#5f5f5f;text-align:right;">{in_t:,} / {out_t:,}</td>'
            f'<td style="padding:2px 0;color:#1a1a1a;text-align:right;font-weight:bold;">${cost:.4f}</td>'
            '</tr>'
        )

    return (
        f'<div style="font-family:{_MONO};font-size:12px;'
        'background:#f7f7f7;border:1px dashed #999;padding:12px 14px;'
        'margin:0 0 8px 0;max-width:600px;">'
        f'<div style="font-weight:bold;color:#1a1a1a;margin-bottom:6px;">'
        f'SLAP DAILY COST &mdash; {cost_date}</div>'
        '<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;">'
        '<tr>'
        '<th style="text-align:left;padding:2px 10px 4px 0;color:#5f5f5f;font-weight:bold;border-bottom:1px solid #ccc;">Pass</th>'
        '<th style="text-align:left;padding:2px 10px 4px 0;color:#5f5f5f;font-weight:bold;border-bottom:1px solid #ccc;">Model</th>'
        '<th style="text-align:right;padding:2px 10px 4px 0;color:#5f5f5f;font-weight:bold;border-bottom:1px solid #ccc;">Tokens (in / out)</th>'
        '<th style="text-align:right;padding:2px 0 4px 0;color:#5f5f5f;font-weight:bold;border-bottom:1px solid #ccc;">Cost</th>'
        '</tr>'
        f'{rows}'
        '<tr>'
        '<td colspan="3" style="padding:6px 10px 2px 0;border-top:1px solid #ccc;color:#1a1a1a;font-weight:bold;">Total</td>'
        f'<td style="padding:6px 0 2px 0;border-top:1px solid #ccc;text-align:right;font-weight:bold;color:#1a1a1a;">${total:.4f}</td>'
        '</tr>'
        '</table>'
        '</div>'
    )


def _copy_marker_html(have_newsletter: bool) -> str:
    """The selection anchor. Everything above it is status; everything below is
    the issue. When there IS no issue, say so instead of pointing at nothing."""
    text = ("↓ Copy newsletter below this line ↓" if have_newsletter
            else "NO NEWSLETTER WAS PRODUCED — nothing to copy")
    colour = "#999" if have_newsletter else "#b42318"
    return ('<div style="text-align:center;font-family:Arial,sans-serif;font-size:11px;'
            f'color:{colour};letter-spacing:.1em;text-transform:uppercase;'
            f'border-top:2px solid {colour};border-bottom:2px solid {colour};'
            'padding:6px 0;margin:0 0 20px 0;">'
            f'{text}</div>')


def _document(body_html: str) -> str:
    """Wrap a body we generated ourselves in a real document.

    MIMEText already labels the part utf-8, but not every client trusts the MIME
    header over the markup, and the panel is full of ✅/❌/— . A bare <body>
    renders as mojibake anywhere that guesses latin-1; the meta tag costs one
    line and removes the guess."""
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '</head>' + body_html + '</html>')


def _github_raw_base():
    """Base raw.githubusercontent URL for the box_score dir on the live branch.
    Uses CI env vars when present, else parses the local git remote."""
    repo = os.getenv("GITHUB_REPOSITORY")
    branch = os.getenv("GITHUB_REF_NAME") or "main"
    if not repo:
        try:
            url = subprocess.check_output(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=SCRIPT_DIR, text=True,
            ).strip()
            m = re.search(r'github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$', url)
            if m:
                repo = f"{m.group(1)}/{m.group(2)}"
        except Exception:
            repo = None
    if not repo:
        return None
    return f"https://raw.githubusercontent.com/{repo}/{branch}/box_score"


def _run_url() -> str:
    """Link back to this GitHub Actions run, for when the log tail is not
    enough. Empty outside CI."""
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo, run_id = os.getenv("GITHUB_REPOSITORY"), os.getenv("GITHUB_RUN_ID")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""


# ---------------------------------------------------------------------------
# building and sending
# ---------------------------------------------------------------------------

def _smtp_send(subject: str, html: str, inline: list) -> None:
    """Send one message. Raises on failure — every caller decides what a
    failure means for it."""
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_pass = os.getenv("GMAIL_PASSWORD")
    if inline:
        msg = MIMEMultipart("related")
        body_wrapper = MIMEMultipart("alternative")
        body_wrapper.attach(MIMEText(html, "html"))
        msg.attach(body_wrapper)
        for cid, img_path in inline:
            img = MIMEImage(img_path.read_bytes(), _subtype="png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=img_path.name)
            msg.attach(img)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(html, "html"))
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg["Subject"] = subject
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, gmail_user, msg.as_string())


def _explain_smtp_failure(e: Exception) -> None:
    # A Gmail app-password error looks exactly like a transient one in the log,
    # and this failure went unnoticed from at least 2026-08-14: the exception
    # was caught, printed, and the step exited 0.
    if "5.7.9" in str(e) or "Application-specific password" in str(e):
        print("    Gmail is rejecting the account password. With 2-step "
              "verification on, GMAIL_PASSWORD must be a 16-character APP "
              "password (Google Account -> Security -> App passwords).")


def build_daily_email(status: dict) -> tuple[str, str, list]:
    """(subject, html, inline_images) for the one daily email."""
    level, headline = pipeline_status.verdict(status)

    newsletter_html = ""
    if SUBSTACK_PATH.exists():
        newsletter_html = SUBSTACK_PATH.read_text(encoding="utf-8")
    have_newsletter = bool(newsletter_html.strip())

    title = "SLAP Newsletter"
    if have_newsletter:
        m = re.search(r'<h1[^>]*>(.*?)</h1>', newsletter_html, re.DOTALL)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip() or title

    # %-d is glibc-only and blows up on Windows, where this file is edited and
    # tested; lstrip is portable and produces the same string.
    today = date.today().strftime("%B %d, %Y").replace(" 0", " ")
    if level == "success":
        subject = f"SLAP {today} — {title}"
    elif level == "partial":
        subject = f"SLAP ⚠ {today} — {title}"
    else:
        subject = f"SLAP ❌ {today} — {headline}"

    header = _status_block_html(status, level, headline)
    pricing = _pricing_block_html()
    if not pricing:
        print("  ⚠ cost_summary.json missing or unreadable — no price breakdown")
    run_url = _run_url()
    if run_url:
        header += (f'<div style="font-family:{_MONO};font-size:11px;color:#8c8c8c;'
                   f'margin:0 0 8px 0;">full job log: '
                   f'<a href="{run_url}" style="color:#8c8c8c;">{run_url}</a></div>')

    inline: list = []
    if not have_newsletter:
        body = (f'<body style="font-family:Arial,sans-serif;">'
                f'{header}{pricing}{_copy_marker_html(False)}'
                '<p style="font-family:Arial,sans-serif;color:#5f5f5f;">'
                'The run did not get as far as producing a newsletter. The stage '
                'table above shows where it stopped.</p></body>')
        return subject, _document(body), inline

    html_content = newsletter_html
    top = header + pricing + _copy_marker_html(True)
    body_match = re.search(r'<body[^>]*>', html_content, re.IGNORECASE)
    if body_match:
        at = body_match.end()
        html_content = html_content[:at] + "\n" + top + "\n" + html_content[at:]
    else:
        html_content = top + "\n" + html_content

    # Box score images, in sorted (numeric-prefix) order. Decide delivery mode by
    # total size: small → embed inline (cid:, self-contained); large → reference
    # GitHub-hosted URLs so the email stays tiny and always sends. Either way the
    # images land in the body under the "Box Scores" header, so a single
    # copy/paste into Substack carries them.
    box_images = sorted(BOX_SCORE_DIR.glob("box_score_sport_*.png"))
    total_raw = sum(p.stat().st_size for p in box_images)
    raw_base = _github_raw_base() if box_images else None
    use_inline = bool(box_images) and (total_raw <= MAX_INLINE_RAW_BYTES or raw_base is None)

    imgs_html = ""
    for i, img_path in enumerate(box_images, 1):
        if use_inline:
            cid = f"boxscore{i:02d}"
            inline.append((cid, img_path))
            src = f"cid:{cid}"
        else:
            src = f"{raw_base}/{img_path.name}"
        imgs_html += f'<img src="{src}" alt="{img_path.stem}" style="{_IMG_STYLE}" />\n'

    if imgs_html:
        marker = re.search(r'(<h2[^>]*>\s*Box Scores\s*</h2>)', html_content, re.IGNORECASE)
        if marker:
            at = marker.end()
            html_content = html_content[:at] + "\n" + imgs_html + html_content[at:]
        elif "</body>" in html_content:
            html_content = html_content.replace("</body>", imgs_html + "</body>", 1)
        else:
            html_content += imgs_html

    if box_images:
        mb = total_raw / 1024 / 1024
        if use_inline:
            print(f"  → {len(box_images)} box score image(s) inline (cid), {mb:.1f}MB raw")
        else:
            print(f"  → {len(box_images)} box score image(s) too large to inline "
                  f"({mb:.1f}MB raw) — using hosted URLs: {raw_base}")
    else:
        print("  ⚠ no box_score_sport_*.png found — body sent without box scores")

    return subject, html_content, inline


def send_daily_email() -> bool:
    """The one daily email. Returns False only if the SEND itself failed —
    a failed pipeline still counts as a successful send."""
    if not os.getenv("GMAIL_ADDRESS") or not os.getenv("GMAIL_PASSWORD"):
        print("  ⚠ GMAIL_ADDRESS or GMAIL_PASSWORD not set — skipping email delivery")
        run_status.record(email_sent=False,
                          email_error="GMAIL_ADDRESS or GMAIL_PASSWORD not set")
        return False

    status = run_status.load()
    level, headline = pipeline_status.verdict(status)
    print(f"  → pipeline {level.upper()}: {headline}")

    subject, html, inline = build_daily_email(status)
    try:
        _smtp_send(subject, html, inline)
    except Exception as e:
        print(f"  ✗ Email failed: {e}")
        _explain_smtp_failure(e)
        run_status.record(email_sent=False, email_error=f"{type(e).__name__}: {e}")
        return False

    print(f"  ✓ Email sent to {os.getenv('GMAIL_ADDRESS')}")
    print(f"  → Subject: {subject}")
    if level != "failed":
        print("  → Open email, select all below the marker, paste into Substack")
    run_status.record(email_sent=True, email_error="", email_subject=subject)
    return True


# ---------------------------------------------------------------------------
# the noon follow-up — sent ONLY when the publish needs attention
# ---------------------------------------------------------------------------

def send_publish_alert(result_path: str, step_outcome: str) -> bool:
    """Second, lightweight email from publish-substack.yml. Sends nothing when
    the publish went fine or was a legitimate no-op (Abram published it himself,
    deleted the draft, scheduled it in Substack). Returns True if it sent."""
    result = {}
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    if not result:
        # publish.py writes a result for every path it takes, including its own
        # exceptions. No file at all means it died before it could — a crashed
        # interpreter, a bad checkout, an install failure.
        if step_outcome == "success":
            print("  no publish result file and the step passed — nothing to report")
            return False
        result = {"outcome": "error", "needs_attention": True,
                  "reason": "the publish step failed before it could report a "
                            "result — see the job log"}

    if not result.get("needs_attention"):
        print(f"  publish outcome '{result.get('outcome')}' needs no email — "
              f"staying quiet")
        return False

    if not os.getenv("GMAIL_ADDRESS") or not os.getenv("GMAIL_PASSWORD"):
        print("  ⚠ GMAIL_ADDRESS/GMAIL_PASSWORD not set — cannot send the alert")
        return False

    reason = str(result.get("reason") or result.get("outcome") or "unknown")
    now = datetime.now(ET).strftime("%I:%M %p").lstrip("0")
    today = datetime.now(ET).strftime("%B %d, %Y").replace(" 0", " ")
    run_url = _run_url()
    icon, colour, bg, _word = _LEVEL["failed"]

    html = (
        f'<body style="font-family:{_MONO};font-size:13px;">'
        f'<div style="background:{bg};border:1px solid {colour};padding:14px;'
        'max-width:600px;">'
        f'<div style="font-weight:bold;color:{colour};font-size:14px;">'
        f'{icon} SUBSTACK PUBLISH NEEDS ATTENTION &mdash; {today}</div>'
        f'<div style="margin-top:6px;color:#1a1a1a;">{_html.escape(reason)}</div>'
        f'<div style="margin-top:10px;color:#5f5f5f;">Today\'s issue is '
        '<b>not live</b>. Publish it by hand from the Substack dashboard, or '
        're-run the publish workflow.</div>'
        + (f'<div style="margin-top:10px;color:#8c8c8c;">job log: '
           f'<a href="{run_url}" style="color:#8c8c8c;">{run_url}</a></div>'
           if run_url else "")
        + f'<div style="margin-top:10px;color:#8c8c8c;">checked at {now} ET</div>'
        '</div></body>'
    )
    html = _document(html)
    subject = f"SLAP ❌ {today} — Substack publish needs attention"
    try:
        _smtp_send(subject, html, [])
    except Exception as e:
        print(f"  ✗ Alert email failed: {e}")
        _explain_smtp_failure(e)
        return False
    print(f"  ✓ Publish alert sent — {reason}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="SLAP email delivery")
    ap.add_argument("--publish-alert", action="store_true",
                    help="noon mode: email ONLY if the Substack publish needs "
                         "attention (silence means it published)")
    ap.add_argument("--result", default="publish_result.json",
                    help="with --publish-alert: publish.py's result file")
    ap.add_argument("--step-outcome", default="success",
                    help="with --publish-alert: the publish step's outcome, so a "
                         "crash that wrote no result file still sends an alert")
    args = ap.parse_args()

    if args.publish_alert:
        print("\n── SUBSTACK PUBLISH CHECK ──────────────────────────")
        send_publish_alert(args.result, args.step_outcome)
        # Always exit 0: whether an alert was warranted is not this script's
        # verdict on the run. The publish step's own exit code already carries
        # that, and a green "nothing to report" must not look like a failure.
        return 0

    print("\n── DAILY STATUS EMAIL ──────────────────────────────")
    # Exit non-zero only when the SEND failed. The workflow step carries
    # continue-on-error so verify_run.py --gate still runs and turns the whole
    # run's outcome into the job's exit code.
    return 0 if send_daily_email() else 1


if __name__ == "__main__":
    sys.exit(main())
