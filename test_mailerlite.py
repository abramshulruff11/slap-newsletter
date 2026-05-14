"""
Quick test — builds newsletter_email.html from newsletter_draft.html,
then fires post_to_mailerlite() without running any AI passes.

Usage:
    python test_mailerlite.py
"""

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(".env")

from build_email_html import build_email_html
from generate_newsletter import post_to_mailerlite

draft_path = Path("newsletter_draft.html")
email_path = Path("newsletter_email.html")

if not draft_path.exists():
    print("✗ newsletter_draft.html not found — run generate_newsletter.py once first")
else:
    print(f"Building email HTML from {draft_path} ({draft_path.stat().st_size:,} bytes)...")
    email_html = build_email_html(draft_path.read_text(encoding="utf-8"))
    email_path.write_text(email_html, encoding="utf-8")
    print(f"✓ newsletter_email.html written ({len(email_html):,} bytes)")
    print(f"  Open newsletter_email.html in a browser to preview before sending.\n")
    post_to_mailerlite(email_html)
