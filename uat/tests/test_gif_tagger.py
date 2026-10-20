"""
Run:  python -X utf8 uat/tests/test_gif_tagger.py

Locks SLA-133: the GIF vision tagger and its review page.

WHAT IS ACTUALLY AT RISK HERE
  1. The model only seeing the first frame. The API reads an animated GIF as
     its first frame, so the tagger sends a strip of frames spread across the
     animation. The strip must include the first AND last frames and must not
     pad a still image into six copies.

  2. Media being kept. The library stays a list of links (the accepted
     Giphy-terms position): nothing but ids and tags may reach disk.

  3. Tags the pipeline can't use. Situation ids are an enum built from the
     taxonomy, moods from the library; anything else, duplicates, and "none"
     mixed with real ids are dropped, and a rejected GIF carries no tags.

  4. The request being one the pinned SDK rejects or mangles. A real
     anthropic.Anthropic client is driven through a mock HTTP transport, so
     the body that would be sent is checked, not a hand-built dict.

  5. The library being touched. The tagger writes proposals elsewhere.

  6. The accuracy bar. The review page's score counts only judged tags, and
     ignores verdicts on tags the tagger no longer proposes.

No network, no API key: Giphy fetches and the HTTP transport are stubbed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "uat"))

from PIL import Image  # noqa: E402

import gif_tagger as GT  # noqa: E402
import review_gif_tags as RV  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


def make_gif(colors: list[tuple[int, int, int]], size=(40, 30)) -> bytes:
    frames = [Image.new("RGB", size, c) for c in colors]
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    return out.getvalue()


LIBRARY = GT.load_json(GT.LIBRARY_PATH)
TAXONOMY = GT.load_json(GT.TAXONOMY_PATH)
IDS = GT.subcategory_ids(TAXONOMY)
MOODS = GT.mood_ids(LIBRARY, TAXONOMY)

print("\n1. Frame strip")
# 10 frames, each a distinct colour; frame 0 red-ish, frame 9 blue-ish.
colors = [(25 * i, 200 - 20 * i, 10 * i) for i in range(10)]
png, used = GT.frame_strip(make_gif(colors))
check("ten-frame GIF gives a six-frame strip", used, 6)
strip = Image.open(io.BytesIO(png)).convert("RGB")
check("strip is FRAME_HEIGHT tall", strip.height, GT.FRAME_HEIGHT)
first = strip.getpixel((2, 2))
last = strip.getpixel((strip.width - 3, 2))
near = lambda a, b: all(abs(x - y) <= 12 for x, y in zip(a, b))  # GIF palette rounding
check_true("strip starts on the first frame", near(first, colors[0]))
check_true("strip ends on the last frame", near(last, colors[-1]))
_png, used = GT.frame_strip(make_gif([(10, 20, 30)]))
check("a still image is one frame, not six copies", used, 1)
_png, used = GT.frame_strip(make_gif(colors[:3]))
check("a three-frame GIF uses all three", used, 3)

print("\n2. Inputs")
entries = GT.verified_entries(LIBRARY)
total_verified = sum(1 for c in LIBRARY["categories"].values() for g in c["gifs"] if g.get("status") == "verified")
check("every verified GIF is a candidate", len(entries), total_verified)
check_true("each candidate carries its category", all(e.get("category") for e in entries))
n_lib = len(LIBRARY["categories"])
check("moods start with the library's, in order", MOODS[:n_lib], list(LIBRARY["categories"]))
check("then the taxonomy's new ones", sorted(MOODS[n_lib:]), ["anticipation", "relief"])
prompt = GT.system_prompt(LIBRARY, TAXONOMY)
check_true("every situation id is in the prompt", all(f"- {i}:" in prompt for i in IDS))
check_true("every mood is in the prompt", all(f"- {m}:" in prompt for m in MOODS))
check_true("the prompt forbids identifying people by face", "Never identify anyone from their face" in prompt)
check_true("the new moods are described, not blank", all(f"- {m}: " in prompt and GT.NEW_MOODS[m] in prompt
                                                         for m in GT.NEW_MOODS))
meta = GT.metadata_text(entries[0], {"title": "Wow GIF by NBA", "slug": "nba-wow-x", "username": "nba",
                                     "user": {"display_name": "NBA"}})
check_true("uploader line keeps the display name intact", "Giphy channel/uploader: nba (NBA)" in meta)
check_true("missing Giphy metadata does not crash", "Giphy title: " in GT.metadata_text(entries[0], None))
check("rendition prefers the small fixed_height",
      GT.pick_rendition({"images": {"original": {"url": "o"}, "fixed_height": {"url": "f"}}}), "f")
check("no rendition -> None", GT.pick_rendition({"images": {}}), None)

print("\n3. Schema")
schema = GT.output_schema(IDS, MOODS)
sit_enum = schema["properties"]["subcategories"]["items"]["properties"]["id"]["enum"]
check("situation enum = taxonomy ids + none", sit_enum, IDS + ["none"])
check("mood enum = MOODS", schema["properties"]["moods"]["items"]["enum"], MOODS)


def closed(node) -> bool:
    if isinstance(node, dict):
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            return False
        if node.get("type") == "object" and set(node.get("required", [])) != set(node.get("properties", {})):
            return False
        return all(closed(v) for v in node.values())
    if isinstance(node, list):
        return all(closed(v) for v in node)
    return True


check_true("every object is closed and fully required (structured-output rules)", closed(schema))

print("\n4. Parsing")


class Block:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class Resp:
    def __init__(self, payload, stop="end_turn"):
        self.stop_reason = stop
        self.content = [Block("thinking"), Block("text", json.dumps(payload))]


base = {"what_happens": "x", "usable": True, "moods": ["celebratory", "celebratory", "made_up", "relief"],
        "reject_reason": "none", "intensity": "high", "reaction_readiness": "ready", "caption_text": "",
        "entities": {"players": [], "teams": [], "figures": [], "league": "nba", "evidence": ""},
        "confidence": "high",
        "subcategories": [{"id": "comeback_euphoria", "fit": "strong"}, {"id": "none", "fit": "partial"},
                          {"id": "comeback_euphoria", "fit": "partial"}, {"id": "not_a_real_id", "fit": "strong"},
                          {"id": "clinch_or_title_party", "fit": "partial"}, {"id": "pure_joy_breakthrough", "fit": "partial"},
                          {"id": "together_again", "fit": "partial"}]}
t = GT.parse_response(Resp(base), IDS, MOODS)
check("drops none, duplicates and unknown ids; caps at three",
      [s["id"] for s in t["subcategories"]], ["comeback_euphoria", "clinch_or_title_party", "pure_joy_breakthrough"])
check("moods de-duplicated and unknown ones dropped", t["moods"], ["celebratory", "relief"])
t = GT.parse_response(Resp({**base, "usable": False, "reject_reason": "interview_or_talking"}), IDS, MOODS)
check("a rejected GIF carries no situations", t["subcategories"], [])
check("a rejected GIF carries no moods", t["moods"], [])
for stop in ("refusal", "max_tokens"):
    try:
        GT.parse_response(Resp(base, stop=stop), IDS, MOODS)
        check(f"stop_reason {stop} raises", False, True)
    except ValueError:
        check(f"stop_reason {stop} raises", True, True)

print("\n5. The request, through the real pinned SDK")
import anthropic  # noqa: E402
import httpx2  # noqa: E402

SENT: list[dict] = []
REPLY = {**base, "moods": ["celebratory"], "subcategories": [{"id": "comeback_euphoria", "fit": "strong"}]}


def handler(request):
    SENT.append({"path": request.url.path, "headers": dict(request.headers), "body": json.loads(request.content)})
    return httpx2.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": GT.MODEL,
        "content": [{"type": "text", "text": json.dumps(REPLY)}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 300,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})


def stub_client():
    return anthropic.Anthropic(api_key="test", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


req = GT.build_request(entries[0], {"title": "t"}, png, LIBRARY, TAXONOMY)
resp = stub_client().beta.messages.create(**req)
body = SENT[-1]["body"]
check("model (Sonnet 5.5, decided 2026-10-15)", body["model"], "claude-sonnet-5-5")
check("structured output is requested", body["output_config"]["format"]["type"], "json_schema")
check("effort is set explicitly (Sonnet 5.5 defaults to high)", body["output_config"]["effort"], "medium")
check("refusal fallback is on, in the only form Sonnet 5.5 accepts", body.get("fallbacks"), "default")
check_true("the fallback beta header is sent", GT.FALLBACK_BETA in SENT[-1]["headers"].get("anthropic-beta", ""))
check_true("no thinking parameter", "thinking" not in body)
check_true("no forced tool_choice (400 on Sonnet 5.5)", "tool_choice" not in body)
check("default prices are Sonnet 5.5's", (GT.PRICE_IN, GT.PRICE_OUT), (2.00, 10.00))
req_opus = GT.build_request(entries[0], {"title": "t"}, png, LIBRARY, TAXONOMY,
                            model="claude-opus-5-5", effort="low")
check("--model / --effort reach the request", (req_opus["model"], req_opus["output_config"]["effort"]),
      ("claude-opus-5-5", "low"))
_u = type("U", (), {"input_tokens": 1_000_000, "output_tokens": 0,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()
check("cost uses the model that served it", (GT.cost(_u), GT.cost(_u, "claude-opus-5-5")), (2.0, 4.0))
img = body["messages"][0]["content"][0]
check("the image is the strip, as PNG", img["source"]["media_type"], "image/png")
check("the image bytes are the strip's", base64.b64decode(img["source"]["data"]), png)
check("system prompt is marked for caching", body["system"][0]["cache_control"]["type"], "ephemeral")
check("the stubbed reply parses", [s["id"] for s in GT.parse_response(resp, IDS, MOODS)["subcategories"]],
      ["comeback_euphoria"])
check_true("cost estimate is positive", GT.cost(resp.usage) > 0)

print("\n6. End to end: the library is untouched and no media reaches disk")
lib_hash = hashlib.sha256(GT.LIBRARY_PATH.read_bytes()).hexdigest()
gif_bytes = make_gif(colors)
fetches = []
with tempfile.TemporaryDirectory() as tmp:
    GT.OUTPUT_PATH = Path(tmp) / "output" / "gif_tags.json"
    GT.make_client = stub_client
    GT.env_value = lambda name: "test-key"
    GT.fetch_giphy = lambda gid, key: (fetches.append(gid) or
                                       {"title": "t", "images": {"fixed_height": {"url": "https://x/g.gif"}}})
    GT.fetch_giphy_many = lambda ids, key: {}   # batched lookup returns nothing: per-id path
    GT.fetch_bytes = lambda url: gif_bytes
    with redirect_stdout(io.StringIO()):
        rc = GT.main(["--limit", "3"])
    check("run exits 0", rc, 0)
    out = json.loads(GT.OUTPUT_PATH.read_text(encoding="utf-8"))
    check("three GIFs tagged", len(out["tags"]), 3)
    first_id = entries[0]["id"]
    check_true("tags are keyed by Giphy id", first_id in out["tags"])
    check("frames_used recorded", out["tags"][first_id]["frames_used"], 6)
    written = list(Path(tmp).rglob("*"))
    check("only the tags file was written", sorted(p.name for p in written if p.is_file()), ["gif_tags.json"])
    raw = GT.OUTPUT_PATH.read_text(encoding="utf-8")
    check_true("no image data in the output", "base64" not in raw and "iVBOR" not in raw and len(raw) < 20_000)
    with redirect_stdout(io.StringIO()):
        GT.main(["--limit", "3"])
    check("a second run skips what is already tagged", len(json.loads(GT.OUTPUT_PATH.read_text())["tags"]), 6)
    n_before = len(fetches)
    with redirect_stdout(io.StringIO()):
        GT.main(["--ids", first_id])
    check("--ids without --retag does nothing for a tagged id", len(fetches), n_before)
    GT.fetch_bytes = lambda url: (_ for _ in ()).throw(OSError("network down"))
    with redirect_stdout(io.StringIO()):
        rc = GT.main(["--ids", first_id, "--retag"])
    check("a total failure exits non-zero", rc, 1)
    check("a failed retag keeps the earlier tags", first_id in json.loads(GT.OUTPUT_PATH.read_text())["tags"], True)

    import urllib.error  # noqa: E402

    def giphy_429(gid, key):
        fetches.append(gid)
        raise urllib.error.HTTPError("https://api.giphy.com", 429, "Too Many Requests", {}, None)

    GT.fetch_giphy = giphy_429
    n_before = len(fetches)
    log = io.StringIO()
    with redirect_stdout(log):
        rc = GT.main(["--limit", "3"])
    check("a Giphy 429 stops the batch after one call", len(fetches) - n_before, 1)
    check("a rate-limited run exits non-zero", rc, 1)
    retry = [l for l in log.getvalue().splitlines() if l.startswith("Failed (re-run to retry): ")]
    check("every untried GIF is listed for the re-run",
          len(retry[0].split(": ", 1)[1].split(",")) if retry else 0, 3)

    # SLA-141: one batched lookup covers the run, so no per-GIF Giphy call.
    GT.fetch_giphy_many = lambda ids, key: {i: {"id": i, "title": "t",
                                                "images": {"fixed_height": {"url": "https://x/g.gif"}}}
                                            for i in ids}
    GT.fetch_bytes = lambda url: gif_bytes
    n_before = len(fetches)
    with redirect_stdout(io.StringIO()):
        rc = GT.main(["--limit", "2"])
    check("batched metadata: the run succeeds", rc, 0)
    check("batched metadata: no per-GIF Giphy call", len(fetches) - n_before, 0)

    def batch_429(ids, key):
        raise urllib.error.HTTPError("https://api.giphy.com", 429, "Too Many Requests", {}, None)

    GT.fetch_giphy_many = batch_429
    n_before = len(fetches)
    with redirect_stdout(io.StringIO()):
        rc = GT.main(["--limit", "3"])
    check("a failed batch falls back to per-id lookups (which stop on their own 429)",
          len(fetches) - n_before, 1)
check("the library file is byte-for-byte unchanged", hashlib.sha256(GT.LIBRARY_PATH.read_bytes()).hexdigest(), lib_hash)

print("\n7. Review page and score")
tags = {"a": {**REPLY, "label": "A", "category": "celebratory",
              "entities": {"players": ["Jalen Brunson"], "teams": [], "figures": [], "league": "nba", "evidence": "title"}},
        "b": {**REPLY, "label": "B", "category": "celebratory", "usable": False, "moods": [], "subcategories": [],
              "reject_reason": "walkout_or_intro"}}
keys = [k for k, _ in RV.tag_keys("a", tags["a"])]
check("judgeable claims on a tagged GIF", keys, ["mood:celebratory", "sit:comeback_euphoria", "ent:players:Jalen Brunson"])
check("a rejected GIF's one claim is the reject call", [k for k, _ in RV.tag_keys("b", tags["b"])], ["reject"])
page = RV.build_page(tags, IDS)
check_true("page embeds each GIF keylessly", "giphy.com/embed/a" in page and "giphy.com/embed/b" in page)
check_true("page offers every situation for misses", all(f'value="{i}"' in page for i in IDS))
check_true("page sets a dark-mode palette", "prefers-color-scheme: dark" in page)
decisions = {"a": {"tags": {"mood:celebratory": True, "sit:comeback_euphoria": True, "sit:stale_tag": False},
                   "missed": ["season_over"]},
             "b": {"tags": {"reject": True}}}
r = RV.score(tags, decisions)
check("verdict on a tag no longer proposed is ignored", r["kinds"]["sit"]["wrong"], 0)
check("situation rate", r["situation_rate"], 1.0)
check("passes the bar", r["passes"], True)
check("misses are counted", r["missed"], {"season_over": 1})
r = RV.score(tags, {})
check("nothing judged -> no rate, not a pass", (r["situation_rate"], r["passes"]), (None, False))
r = RV.score(tags, {"a": {"tags": {"sit:comeback_euphoria": False}}})
check("one wrong of one fails the bar", r["passes"], False)

print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — 0 network requests, 0 API calls")
print("=" * 72)
