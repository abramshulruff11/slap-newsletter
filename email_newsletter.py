"""
SLAP Newsletter — Email Delivery
Sends newsletter_substack.html as the email body (rendered HTML) after each run.
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

SCRIPT_DIR    = Path(__file__).resolve().parent
SUBSTACK_PATH = SCRIPT_DIR / "newsletter_substack.html"


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

    # Send as inline HTML — the newsletter IS the email body.
    # Open on your phone, select all, copy, paste into Substack.
    # Gmail renders the HTML; Substack receives clean rich text, not code.
    msg = MIMEMultipart("alternative")
    msg["From"]    = gmail_user
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_content, "html"))

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
