"""
SLAP Newsletter — Email Delivery
Sends newsletter_substack.html as the email body (rendered HTML) after each run.
The per-sport box score images are embedded INLINE in the body (via cid:) under
the "Box Scores" header, in order — so a single select-all → copy → paste into
Substack carries the whole issue, images included. No attachments to download.
Triggered by GitHub Actions after generate_newsletter.py completes.
"""

import os
import re
import smtplib
import subprocess
from pathlib import Path
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

SCRIPT_DIR      = Path(__file__).resolve().parent
SUBSTACK_PATH   = SCRIPT_DIR / "newsletter_substack.html"
BOX_SCORE_DIR   = SCRIPT_DIR / "box_score"

# Gmail rejects messages over 25 MB (and MIME base64 inflates binary by ~37%).
# Below this RAW total we embed the box scores inline (cid:) — self-contained,
# single copy/paste. Above it, we fall back to GitHub-hosted <img> URLs so the
# email stays tiny no matter how huge the slate (NFL Sundays, CFB Saturdays,
# March Madness). 15 MB raw ≈ 20.5 MB encoded — comfortably under the limit.
MAX_INLINE_RAW_BYTES = 15 * 1024 * 1024

_IMG_STYLE = "display:block;width:100%;max-width:680px;height:auto;margin:16px auto;"


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


def send_email():
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_pass = os.getenv("GMAIL_PASSWORD")
    to_email   = os.getenv("GMAIL_ADDRESS")  # send to yourself

    if not gmail_user or not gmail_pass:
        print("  ⚠ GMAIL_ADDRESS or GMAIL_PASSWORD not set — skipping email delivery")
        return

    if not SUBSTACK_PATH.exists():
        print("  ✗ newsletter_substack.html not found — skipping email delivery")
        return

    html_content = SUBSTACK_PATH.read_text(encoding="utf-8")

    # Extract title from first <h1> for subject line (do this before injecting imgs)
    title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else "SLAP Newsletter"
    today = date.today().strftime("%B %-d, %Y")
    subject = f"SLAP {today} — {title}"

    # Box score images, in sorted (numeric-prefix) order. Decide delivery mode by
    # total size: small → embed inline (cid:, self-contained); large → reference
    # GitHub-hosted URLs so the email stays tiny and always sends. Either way the
    # images land in the body under the "Box Scores" header, so a single
    # copy/paste into Substack carries them.
    box_images = sorted(BOX_SCORE_DIR.glob("box_score_sport_*.png"))
    total_raw = sum(p.stat().st_size for p in box_images)
    raw_base = _github_raw_base() if box_images else None
    use_inline = bool(box_images) and (total_raw <= MAX_INLINE_RAW_BYTES or raw_base is None)

    inline = []      # (cid, path) — only populated in inline mode
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

    # related (with cid parts) for inline mode; plain alternative for hosted URLs.
    if inline:
        msg = MIMEMultipart("related")
    else:
        msg = MIMEMultipart("alternative")
    msg["From"]    = gmail_user
    msg["To"]      = to_email
    msg["Subject"] = subject

    if inline:
        body_wrapper = MIMEMultipart("alternative")
        body_wrapper.attach(MIMEText(html_content, "html"))
        msg.attach(body_wrapper)
        for cid, img_path in inline:
            img = MIMEImage(img_path.read_bytes(), _subtype="png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=img_path.name)
            msg.attach(img)
    else:
        msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        print(f"  ✓ Email sent to {to_email}")
        print(f"  → Subject: {subject}")
        print(f"  → Open email, select all, copy, paste into Substack (images included)")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")


if __name__ == "__main__":
    print("\n── EMAIL DELIVERY ──────────────────────────────────")
    send_email()
