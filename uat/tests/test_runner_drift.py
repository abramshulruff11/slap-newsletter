"""
Run:  python -X utf8 uat/tests/test_runner_drift.py

Locks the divergence between the two runners, generate_newsletter.py and
uat/generate_newsletter_uat.py.

Two failures on 2026-09-01 came from the same cause: a change was applied to
one runner and not the other.

  - Pass 1's max_tokens was raised 16,384 -> 32,768 in production without the
    streaming call UAT already had. The run died client-side, shipped nothing.
  - The GIF library prompts were promoted, teaching Pass 2 to emit
    data-library-category placeholders, but production got no consumer for
    them. Seven GIFs shipped as invisible empty divs.

Neither was detectable. promote.py diffs *prompts* only, and the other suites
stub the Anthropic client, so runner-code drift was structurally invisible to
every check in the repo. This is that check.

The rule: any function defined in BOTH runners must be byte-identical, unless
it is declared in KNOWN_DIVERGENT below with a reason. A stale entry — declared
divergent but now identical — also fails, so the ledger shrinks as the two
trees converge and can never quietly over-state the problem.

No API calls, no network.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROD = REPO / "generate_newsletter.py"
UAT = REPO / "uat" / "generate_newsletter_uat.py"


# Declared, understood divergences. Each entry is a debt with a reason, not a
# blessing. Delete the entry when the pair converges — the test enforces that.
#
# The recorded hashes are what stop an allowlist from becoming a blind spot.
# Being declared exempts a pair from the identical-source rule, and these four
# are the highest-traffic functions in the pipeline — run_pass1 is where the
# 2026-09-01 streaming outage happened, a change made to production alone. If
# "declared" also meant "unchecked", the test would be blind to a repeat of the
# exact failure it exists to prevent. So each side's normalized source is
# pinned: touch either copy and this test fails until you have looked at the
# counterpart and deliberately re-recorded the hash.
KNOWN_DIVERGENT = {
    "run_pass1": dict(prod="e22669a9998d", uat="19da9bdf0a57", reason=(
        "Bidirectional, and the two now hold DIFFERENT video policies on "
        "purpose. Prod (2026-09-04): video tweets are tagged by fetch_content, "
        "marked for Pass 1 in the payload, and removed from the headliners by "
        "plan_audit.enforce_video_policy — Around the League keeps them, "
        "because a clip there interrupts no writing. UAT: video tweets are "
        "dropped from Pass 1's candidate list entirely (§2.1), because Pass 1B "
        "turns them into highlight GIFs and prod has no Pass 1B. Prod also has "
        "degraded mode (Nitter outage -> headline-only). Neither side is a "
        "superset, so no copy in either direction is safe. (2026-09-04: meme "
        "cooldown — the recent-slug block and the same-engine swap — was added "
        "to BOTH copies identically.)"
    )),
    "run_pass2": dict(prod="bfabc3173843", uat="7db198bedbf7", reason=(
        "Bidirectional. Prod carries degraded-mode wiring UAT lacks; UAT "
        "carries highlight-plan wiring prod has no Pass 1B for. (2026-09-04: "
        "the {{MEME_SELECTOR_INDEX}} substitution was added to BOTH copies "
        "identically — the meme library is now the only template list the "
        "writer sees. 2026-09-05: the bounded tool loop, MAX_TOKENS_WRITER and "
        "the was_truncated check likewise went into both.)"
    )),
    "pre_edit": dict(prod="5b2c136f2496", uat="73360843476a", reason=(
        "Small drift in both directions; not yet reconciled."
    )),
    "main": dict(prod="585fb6a5e3fe", uat="0e25f4ba71c9", reason=(
        "Not real drift. UAT's entry point is run_uat.py, so its main() is a "
        "5-line stub. Expected to stay divergent permanently. (2026-09-04: prod "
        "now strips HTML comments from the published Substack file. Nothing to "
        "port — run_uat.py writes only the draft template; the UAT runner's "
        "SUBSTACK_OUTPUT_PATH is unused. It also now hands game_state to Pass 6 "
        "for CHECK 8; run_uat.py does the same at its own call site. 2026-09-05: "
        "prod's main() calls run_status.reset(); UAT instead repoints "
        "run_status.STATUS_PATH at its own output dir at import, so the sandbox "
        "never writes prod's status file.)"
    )),
}


def fingerprint(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def top_level_functions(path: Path) -> dict:
    """name -> (normalized source, line count) for module-level defs."""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = lines[node.lineno - 1:node.end_lineno]
            normalized = "\n".join(
                ln.rstrip() for ln in body if ln.strip()
            )
            out[node.name] = (normalized, node.end_lineno - node.lineno + 1)
    return out


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


prod = top_level_functions(PROD)
uat = top_level_functions(UAT)
shared = sorted(set(prod) & set(uat))

print("=" * 66)
print("RUNNER DRIFT — generate_newsletter.py vs uat/generate_newsletter_uat.py")
print("=" * 66)
print(f"  prod functions: {len(prod)}   uat functions: {len(uat)}   "
      f"shared: {len(shared)}")

diverged = {n for n in shared if prod[n][0] != uat[n][0]}
identical = [n for n in shared if n not in diverged]
print(f"  identical: {len(identical)}   diverged: {len(diverged)}")
print()

# 1. Nothing may diverge that has not been declared.
undeclared = sorted(diverged - set(KNOWN_DIVERGENT))
if undeclared:
    print("  UNDECLARED DIVERGENCE — a change reached one runner, not the other:")
    for name in undeclared:
        print(f"    - {name}()  prod {prod[name][1]} lines / uat {uat[name][1]} lines")
    print()
    print("  Apply the change to both copies, or declare it in KNOWN_DIVERGENT")
    print("  with the reason it cannot yet be reconciled.")
check("no undeclared divergence", undeclared, [])

# 2. The ledger may not over-state the debt. A pair that has converged must be
#    removed from KNOWN_DIVERGENT, or the list slowly becomes a list of lies.
stale = sorted(set(KNOWN_DIVERGENT) & set(identical))
if stale:
    print("  STALE LEDGER ENTRIES — these converged; delete them from")
    print("  KNOWN_DIVERGENT so the remaining entries stay meaningful:")
    for name in stale:
        print(f"    - {name}()")
check("no stale KNOWN_DIVERGENT entries", stale, [])

# 3. A declared entry that no longer exists in both runners is also stale.
orphaned = sorted(set(KNOWN_DIVERGENT) - set(shared))
check("no KNOWN_DIVERGENT entry for a non-shared function", orphaned, [])

# 4. A declared pair is exempt from the identical-source rule, so its CONTENT
#    is pinned instead. Otherwise the four riskiest functions in the pipeline
#    would be the four with no drift protection at all.
print()
print("  Declared divergences (the outstanding debt):")
drifted = []
for name in sorted(KNOWN_DIVERGENT):
    if name not in shared:
        continue
    rec = KNOWN_DIVERGENT[name]
    got = {"prod": fingerprint(prod[name][0]), "uat": fingerprint(uat[name][0])}
    moved = [side for side in ("prod", "uat") if got[side] != rec[side]]
    flag = "" if not moved else f"   <-- CHANGED: {', '.join(moved)}"
    print(f"    {name}()  prod {prod[name][1]} / uat {uat[name][1]} lines{flag}")
    if moved:
        drifted.append((name, moved, got))

if drifted:
    print()
    print("  A declared-divergent function changed. These are exempt from the")
    print("  identical-source rule, so their content is pinned instead — this is")
    print("  the prompt to check the OTHER runner before moving on.")
    for name, moved, got in drifted:
        print(f"    - {name}(): changed in {', '.join(moved)}")
        print(f"        record: prod=\"{got['prod']}\", uat=\"{got['uat']}\"")
    print("  If the counterpart is handled (or deliberately still diverges),")
    print("  paste those hashes into KNOWN_DIVERGENT to re-arm the check.")
check("no unreviewed change to a declared-divergent function",
      [d[0] for d in drifted], [])

dup_loc = sum(max(prod[n][1], uat[n][1]) for n in identical)
print()
print(f"  Duplicated LOC across identical functions: {dup_loc}")
print("  (every line of that is a future half-port; move it to a root module)")

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
