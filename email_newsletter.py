"""
SLAP Newsletter — Email Delivery
Sends newsletter_substack.html as the email body (rendered HTML) after each run.
The per-sport box score images are embedded INLINE in the body (via cid:) under
the "Box Scores" header, in order — so a single select-all → copy → paste into
Substack carries the whole issue, images included. No attachments to download.
Triggered by GitHub Actions after generate_newsletter.py completes.
"""

import os
import smtplib
import re
from pathlib import Path
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

SCRIPT_DIR      = Path(__file__).resolve().parent
SUBSTACK_PATH   = SCRIPT_DIR / "newsletter_substack.html"
BOX_SCORE_DIR   = SCRIPT_DIR / "box_score"


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

    # Build inline <img> tags for the box score images and inject them into the
    # body under the "Box Scores" header, in sorted (numeric-prefix) order. Each
    # references a cid: that we attach below as an inline image part — so they
    # render in the email body and travel with a single copy/paste into Substack.
    box_images = sorted(BOX_SCORE_DIR.glob("box_score_sport_*.png"))
    inline = []  # (cid, path)
    imgs_html = ""
    for i, img_path in enumerate(box_images, 1):
        cid = f"boxscore{i:02d}"
        inline.append((cid, img_path))
        imgs_html += (
            f'<img src="cid:{cid}" alt="{img_path.stem}" '
            f'style="display:block;width:100%;max-width:680px;height:auto;'
            f'margin:16px auto;" />\n'
        )

    if imgs_html:
        marker = re.search(r'(<h2[^>]*>\s*Box Scores\s*</h2>)', html_content, re.IGNORECASE)
        if marker:
            at = marker.end()
            html_content = html_content[:at] + "\n" + imgs_html + html_content[at:]
        elif "</body>" in html_content:
            html_content = html_content.replace("</body>", imgs_html + "</body>", 1)
        else:
            html_content += imgs_html

    # multipart/related so the cid: images render inline within the HTML body.
    msg = MIMEMultipart("related")
    msg["From"]    = gmail_user
    msg["To"]      = to_email
    msg["Subject"] = subject

    body_wrapper = MIMEMultipart("alternative")
    body_wrapper.attach(MIMEText(html_content, "html"))
    msg.attach(body_wrapper)

    for cid, img_path in inline:
        img_data = img_path.read_bytes()
        img = MIMEImage(img_data, _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=img_path.name)
        msg.attach(img)
        print(f"  → Inline {img_path.name} ({len(img_data) // 1024}KB) as cid:{cid}")
    if inline:
        print(f"  → {len(inline)} box score image(s) embedded inline")
    else:
        print(f"  ⚠ no box_score_sport_*.png found — body sent without box scores")

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
