"""
generate_memes.py — SLAP Newsletter Meme Pipeline

Parses meme-placeholder divs from newsletter HTML, calls the Imgflip API
to generate real meme images, and replaces placeholders with <img> tags.

Usage:
    python generate_memes.py --input newsletter_draft.html --output newsletter_draft.html

Environment variables required:
    IMGFLIP_USERNAME — your Imgflip account username (free tier works)
    IMGFLIP_PASSWORD — your Imgflip account password

Imgflip free tier: images will have a small watermark. Upgrade account to remove.
"""

import os
import re
import sys
import json
import argparse
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# ---------------------------------------------------------------------------
# Curated starter library: slug → Imgflip template ID
# These are the 10 hand-picked templates the agent is instructed to use.
# The live top-100 fetch supplements this but never overrides it.
# ---------------------------------------------------------------------------
CURATED_TEMPLATES = {
    "drake":               "181913649",
    "gru-plan":            "131940431",
    "two-buttons":         "87743020",
    "distracted-boyfriend":"112126428",
    "this-is-fine":        "55311130",
    "expanding-brain":     "93895088",
    "mocking-spongebob":   "102156234",
    "one-does-not-simply": "61579",
    "waiting-skeleton":    "4087833",
    "change-my-mind":      "129242436",
}

IMGFLIP_CAPTION_URL = "https://api.imgflip.com/caption_image"
IMGFLIP_MEMES_URL   = "https://api.imgflip.com/get_memes"


def fetch_live_templates() -> dict:
    """
    Hit the Imgflip get_memes endpoint and return a slug→id map of
    the current top-100 trending templates. This keeps the library
    fresh without manual updates as new memes go viral.

    Slugs are derived by lowercasing the name and replacing spaces with hyphens.
    Curated templates take priority if there's a name collision.
    """
    try:
        resp = requests.get(IMGFLIP_MEMES_URL, timeout=10)
        data = resp.json()
        if not data.get("success"):
            print("[memes] Warning: get_memes API returned failure. Using curated list only.")
            return {}

        live = {}
        for meme in data["data"]["memes"]:
            slug = meme["name"].lower().replace(" ", "-").replace("_", "-")
            # remove special characters
            slug = re.sub(r"[^a-z0-9\-]", "", slug)
            live[slug] = meme["id"]

        print(f"[memes] Fetched {len(live)} live templates from Imgflip.")
        return live

    except Exception as e:
        print(f"[memes] Warning: Could not fetch live templates: {e}. Using curated list only.")
        return {}


def build_template_map() -> dict:
    """Merge live top-100 with curated list. Curated slugs always win."""
    live = fetch_live_templates()
    combined = {**live, **CURATED_TEMPLATES}  # curated overwrites live on collision
    return combined


def generate_meme(template_id: str, top_text: str, bottom_text: str,
                  username: str, password: str, template_slug: str = "") -> str | None:
    """
    Call Imgflip caption_image API. Returns the image URL on success, None on failure.
    Free tier adds a watermark. Paid account ($9.99/mo) removes it — no code change needed.
    """
    payload = {
        "template_id": template_id,
        "username":    username,
        "password":    password,
        "text0":       top_text,
        "text1":       bottom_text,
    }

    # Gru's Plan is a 4-panel template. Panel 3 repeats panel 1 (that's the joke).
    # Panel 4 is Gru's horrified reaction — no text needed.
    if template_slug == "gru-plan":
        payload["text2"] = top_text
        payload["text3"] = ""

    try:
        resp = requests.post(IMGFLIP_CAPTION_URL, data=payload, timeout=10)
        data = resp.json()

        if data.get("success"):
            url = data["data"]["url"]
            print(f"[memes] Generated meme: {url}")
            return url
        else:
            print(f"[memes] Imgflip API error: {data.get('error_message', 'Unknown error')}")
            return None

    except Exception as e:
        print(f"[memes] Request failed: {e}")
        return None


def mocking_spongebob_caps(text: str) -> str:
    """Auto-apply alternating caps for mocking-spongebob bottom text."""
    result = []
    upper = True
    for char in text:
        if char.isalpha():
            result.append(char.upper() if upper else char.lower())
            upper = not upper
        else:
            result.append(char)
    return "".join(result)


def process_newsletter(html: str, template_map: dict, username: str, password: str) -> str:
    """
    Parse all meme-placeholder divs from the newsletter HTML,
    generate real images via Imgflip, and replace with <img> tags.
    Returns updated HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    placeholders = soup.find_all("div", class_="meme-placeholder")

    if not placeholders:
        print("[memes] No meme placeholders found in newsletter.")
        return html

    print(f"[memes] Found {len(placeholders)} meme placeholder(s).")
    replaced = 0
    failed  = 0

    for div in placeholders:
        template_slug = div.get("data-template", "").strip().lower()
        top_text      = div.get("data-top", "").strip()
        bottom_text   = div.get("data-bottom", "").strip()

        if not template_slug:
            print(f"[memes] Skipping placeholder with no data-template.")
            failed += 1
            continue

        # Auto-format mocking-spongebob: Imgflip forces uppercase so alternating caps
        # have no effect via API. The template's visual does the mocking without it.

        template_id = template_map.get(template_slug)
        if not template_id:
            print(f"[memes] Unknown template slug: '{template_slug}'. Skipping.")
            failed += 1
            continue

        img_url = generate_meme(template_id, top_text, bottom_text, username, password, template_slug)

        if img_url:
            # Replace placeholder with centered image
            img_tag = soup.new_tag("div", style="text-align:center; margin: 16px 0;")
            img = soup.new_tag(
                "img",
                src=img_url,
                alt=f"{template_slug} meme",
                style="max-width:100%; border-radius:4px;"
            )
            img_tag.append(img)
            div.replace_with(img_tag)
            replaced += 1
        else:
            # Leave placeholder in place but add a comment so it's visible in review
            div.string = f"[MEME FAILED: {template_slug} | {top_text} | {bottom_text}]"
            failed += 1

    print(f"[memes] Done. {replaced} meme(s) generated, {failed} failed/skipped. Cost: $0.00 (Imgflip free tier)")
    return str(soup)


def main():
    parser = argparse.ArgumentParser(description="SLAP Meme Pipeline")
    parser.add_argument("--input",  required=True, help="Path to newsletter HTML file")
    parser.add_argument("--output", required=True, help="Path to write updated HTML")
    args = parser.parse_args()

    username = os.environ.get("IMGFLIP_USERNAME")
    password = os.environ.get("IMGFLIP_PASSWORD")

    if not username or not password:
        print("[memes] ERROR: IMGFLIP_USERNAME and IMGFLIP_PASSWORD must be set in environment.")
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[memes] ERROR: Input file not found: {input_path}")
        sys.exit(1)

    html = input_path.read_text(encoding="utf-8")
    template_map = build_template_map()
    updated_html = process_newsletter(html, template_map, username, password)

    Path(args.output).write_text(updated_html, encoding="utf-8")
    print(f"[memes] Output written to {args.output}")


if __name__ == "__main__":
    main()
