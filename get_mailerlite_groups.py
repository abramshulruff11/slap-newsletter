"""
Lists all MailerLite groups with their IDs.
Run once to find your SLAP Subscribers group ID, then add it to .env.

Usage:
    python get_mailerlite_groups.py
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv(".env")

api_key = os.getenv("MAILERLITE_API_KEY")
if not api_key:
    raise SystemExit("MAILERLITE_API_KEY not set in .env")

resp = requests.get(
    "https://connect.mailerlite.com/api/groups",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=10,
)

if resp.status_code != 200:
    print(f"✗ Failed ({resp.status_code}): {resp.text[:300]}")
else:
    groups = resp.json().get("data", [])
    if not groups:
        print("No groups found — create one in MailerLite first (Subscribers → Groups → Create group)")
    else:
        print(f"{'ID':<25} {'Name':<30} {'Subscribers'}")
        print("-" * 65)
        for g in groups:
            print(f"{g['id']:<25} {g['name']:<30} {g['active_count']}")
        print("\nCopy the ID for your SLAP group and add to .env as MAILERLITE_GROUP_ID=")
