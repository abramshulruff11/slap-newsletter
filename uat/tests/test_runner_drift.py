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
KNOWN_DIVERGENT = {
    "run_pass1": (
        "Bidirectional. Prod has degraded mode (Nitter outage -> headline-only "
        "newsletter); UAT has the §2.1 video-tweet filter and Pass 1B. Neither "
        "is a superset, so no copy in either direction is safe — this one needs "
        "a real merge."
    ),
    "run_pass2": (
        "Bidirectional. Prod carries degraded-mode wiring UAT lacks; UAT "
        "carries highlight-plan wiring prod has no Pass 1B for."
    ),
    "pre_edit": (
        "Small drift in both directions; not yet reconciled."
    ),
    "main": (
        "Not real drift. UAT's entry point is run_uat.py, so its main() is a "
        "5-line stub. Expected to stay divergent permanently."
    ),
}


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

print()
print("  Declared divergences (the outstanding debt):")
for name in sorted(KNOWN_DIVERGENT):
    if name in shared:
        print(f"    {name}()  prod {prod[name][1]} / uat {uat[name][1]} lines")

dup_loc = sum(max(prod[n][1], uat[n][1]) for n in identical)
print()
print(f"  Duplicated LOC across identical functions: {dup_loc}")
print("  (every line of that is a future half-port; move it to a root module)")

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
