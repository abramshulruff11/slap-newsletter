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
    # --- COMPARISON / PREFERENCE ---
    "drake":                                    "181913649",   # ✓ verified
    "distracted-boyfriend":                     "112126428",   # ✓ verified
    "left-exit-12-off-ramp":                    "124822590",   # ✓ verified
    "two-buttons":                              "87743020",    # ✓ verified
    "corporates-want-you-to-find-the-difference": "180190441", # ✓ verified (They're The Same Picture)
    "buff-doge-vs-cheems":                      "247375501",   # ✓ verified
    "epic-handshake":                           "135256802",   # ✓ verified
    # --- ESCALATION / LEVELS ---
    "expanding-brain":                          "93895088",    # ✓ verified
    "vince-mcmahon-reaction":                   "193966043",   # FIXED: was 27813981 (Hide the Pain Harold)
    "gru-plan":                                 "131940431",   # ✓ verified
    "clown-applying-makeup":                    "195515965",   # FIXED: was 178591752 (Tuxedo Winnie the Pooh)
    "panik-kalm-panik":                         "226297822",   # ✓ verified
    # --- DENIAL / COPIUM ---
    "this-is-fine":                             "55311130",    # ✓ verified
    "hide-the-pain-harold":                     "27813981",    # FIXED: was 27865 (unknown)
    "anakin-padme":                             "322841258",   # FIXED: was 371605855 (unknown)
    "bernie-i-am-once-again-asking":            "222403160",   # FIXED: was 382370190 (unknown)
    # --- REACTION / SURPRISE ---
    "surprised-pikachu":                        "155067746",   # ✓ verified
    "always-has-been":                          "252600902",   # ✓ verified
    "monkey-puppet":                            "148909805",   # ✓ verified
    "mocking-spongebob":                        "102156234",   # ✓ verified
    "first-time":                               "277489984",   # FIXED: was 161865971 (Marked Safe From)
    # --- DOMINATION / SUPERIORITY ---
    "trade-offer":                              "309868304",   # ✓ verified
    "one-does-not-simply":                      "61579",       # ✓ verified
    "waiting-skeleton":                         "4087833",     # ✓ verified
    # --- BETRAYAL / SELF-DESTRUCTION ---
    "spider-man-pointing-at-spider-man":        "122757825",   # FIXED: was 119215120 (unknown)
    "eric-andre-shooting":                      "135678846",   # FIXED: was 97984 (Disaster Girl)
    "is-this-a-pigeon":                         "100777631",   # FIXED: was 100947 (Matrix Morpheus)
    "woman-yelling-at-cat":                     "188390779",   # ✓ verified
    # --- RESIGNATION / WALKING AWAY ---
    "ight-imma-head-out":                       "196652226",   # FIXED: was 378389 (unknown) — Spongebob Ight Imma Head Out
    # --- DEBATE / TAKES ---
    "change-my-mind":                           "129242436",   # ✓ verified
}

IMGFLIP_CAPTION_URL = "https://api.imgflip.com/caption_image"
IMGFLIP_MEMES_URL   = "https://api.imgflip.com/get_memes"


# ---------------------------------------------------------------------------
# Meme history — prevents same template reuse within 7 days
# Mirrors the gif_history.json pattern used in the GIF pipeline.
# ---------------------------------------------------------------------------

def load_meme_history(repo_root: Path) -> list:
    """Load meme_history.json to check for recently used templates."""
    history_path = repo_root / "meme_history.json"
    if history_path.exists():
        try:
            return json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def is_template_recently_used(slug: str, history: list, days: int = 7) -> bool:
    """Return True if this template slug was used in the past N days."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    for entry in history:
        try:
            entry_date = date.fromisoformat(entry["date"])
            if entry_date >= cutoff and entry.get("slug") == slug:
                return True
        except Exception:
            continue
    return False


def save_meme_history(repo_root: Path, new_entries: list, history: list):
    """Append new meme uses to meme_history.json, keep last 60 entries."""
    history_path = repo_root / "meme_history.json"
    combined = new_entries + history
    combined = combined[:60]
    history_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")


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
                  username: str, password: str, template_slug: str = "",
                  middle_text: str = "") -> str | None:
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

    # Panik-Kalm-Panik is a 3-panel template:
    #   text0 = first PANIK  (data-top)
    #   text1 = KALM         (data-middle)
    #   text2 = second PANIK (data-bottom)
    # Override the defaults so middle and bottom map to the correct panels.
    if template_slug == "panik-kalm-panik":
        payload["text1"] = middle_text   # KALM → panel 2
        payload["text2"] = bottom_text   # second PANIK → panel 3

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


def process_newsletter(html: str, template_map: dict, username: str, password: str,
                       repo_root: Path = None) -> tuple[str, list]:
    """
    Parse all meme-placeholder divs from the newsletter HTML,
    generate real images via Imgflip, and replace with <img> tags.
    Returns (updated HTML, list of meme history entries).
    """
    soup = BeautifulSoup(html, "html.parser")
    placeholders = soup.find_all("div", class_="meme-placeholder")

    if not placeholders:
        print("[memes] No meme placeholders found in newsletter.")
        return html, []

    print(f"[memes] Found {len(placeholders)} meme placeholder(s).")

    history = load_meme_history(repo_root) if repo_root else []
    replaced = 0
    failed   = 0
    used_memes = []

    for div in placeholders:
        template_slug = div.get("data-template", "").strip().lower()
        top_text      = div.get("data-top", "").strip()
        middle_text   = div.get("data-middle", "").strip()
        bottom_text   = div.get("data-bottom", "").strip()

        if not template_slug:
            print(f"[memes] Skipping placeholder with no data-template.")
            failed += 1
            continue

        # Warn if this template was used recently but proceed anyway —
        # with only 10 curated slugs and 2-3 memes per issue, a hard
        # block would leave the writer with no options on busy weeks.
        if is_template_recently_used(template_slug, history):
            print(f"[memes] ⚠ '{template_slug}' used in last 7 days — consider varying template")

        template_id = template_map.get(template_slug)
        if not template_id:
            print(f"[memes] Unknown template slug: '{template_slug}'. Skipping.")
            failed += 1
            continue

        img_url = generate_meme(template_id, top_text, bottom_text, username, password, template_slug, middle_text)

        if img_url:
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
            entry = {
                "date": __import__('datetime').date.today().isoformat(),
                "slug": template_slug,
                "top_text": top_text,
                "bottom_text": bottom_text,
            }
            used_memes.append(entry)
            history.insert(0, entry)  # update in-memory history for same-run dedup
        else:
            div.string = f"[MEME FAILED: {template_slug} | {top_text} | {bottom_text}]"
            failed += 1

    print(f"[memes] Done. {replaced} meme(s) generated, {failed} failed/skipped.")

    if repo_root and used_memes:
        save_meme_history(repo_root, used_memes, history)

    return str(soup), used_memes


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
    updated_html, _ = process_newsletter(html, template_map, username, password, repo_root=input_path.parent)

    Path(args.output).write_text(updated_html, encoding="utf-8")
    print(f"[memes] Output written to {args.output}")


if __name__ == "__main__":
    main()
