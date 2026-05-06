"""
SLAP Newsletter — Email Delivery
Sends newsletter_substack.html as an HTML email attachment after each run.
Triggered by GitHub Actions after generate_newsletter.py completes.
"""

import os
import smtplib
import re
from pathlib import Path
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

SCRIPT_DIR = Path(__file__).resolve().parent
SUBSTACK_PATH = SCRIPT_DIR / "newsletter_substack.html"


def send_email():
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    to_email   = os.getenv("GMAIL_USER")  # send to yourself

    if not gmail_user or not gmail_pass:
        print("  ⚠ GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping email delivery")
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

    msg = MIMEMultipart("mixed")
    msg["From"]    = gmail_user
    msg["To"]      = to_email
    msg["Subject"] = subject

    # Plain-text body so the email isn't blank if HTML fails to load
    body = MIMEText(
        f"SLAP Newsletter — {today}\n\n"
        f"Newsletter attached as HTML file.\n"
        f"Open the attachment, copy all, paste into Substack editor.\n\n"
        f"Lead story: {title}",
        "plain"
    )
    msg.attach(body)

    # Attach the Substack-ready HTML file
    attachment = MIMEBase("text", "html")
    attachment.set_payload(html_content.encode("utf-8"))
    encoders.encode_base64(attachment)
    filename = f"SLAP_{date.today().strftime('%Y-%m-%d')}.html"
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        print(f"  ✓ Email sent to {to_email}")
        print(f"  → Subject: {subject}")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")


if __name__ == "__main__":
    print("\n── EMAIL DELIVERY ──────────────────────────────────")
    send_email()
