"""
SLAP Newsletter — Email Delivery
Sends newsletter_substack.html as the email body (rendered HTML) after each run.
Also attaches box_score.png when present — save the attachment, upload to Substack.
Open the email on your phone, select all, copy, paste into Substack.
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

    # Extract title from first <h1> for subject line
    title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else "SLAP Newsletter"
    today = date.today().strftime("%B %-d, %Y")
    subject = f"SLAP {today} — {title}"

    # Use mixed so we can carry both the HTML body and image attachment
    msg = MIMEMultipart("mixed")
    msg["From"]    = gmail_user
    msg["To"]      = to_email
    msg["Subject"] = subject

    # HTML body in its own alternative wrapper (best practice for mixed+html)
    body_wrapper = MIMEMultipart("alternative")
    body_wrapper.attach(MIMEText(html_content, "html"))
    msg.attach(body_wrapper)

    # Box score images — one per sport (MLB chunked). Attach each so they can be
    # saved and uploaded to Substack as image blocks. Sorted for stable order.
    box_images = sorted(BOX_SCORE_DIR.glob("box_score_sport_*.png"))
    if box_images:
        for img_path in box_images:
            img_data = img_path.read_bytes()
            img = MIMEImage(img_data, _subtype="png", name=img_path.name)
            img.add_header("Content-Disposition", "attachment", filename=img_path.name)
            msg.attach(img)
            print(f"  → Attached {img_path.name} ({len(img_data) // 1024}KB)")
        print(f"  → {len(box_images)} box score image(s) attached")
    else:
        print(f"  ⚠ no box_score_sport_*.png found — skipping attachments")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        print(f"  ✓ Email sent to {to_email}")
        print(f"  → Subject: {subject}")
        print(f"  → Open email, select all, copy, paste into Substack")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")


if __name__ == "__main__":
    print("\n── EMAIL DELIVERY ──────────────────────────────────")
    send_email()
