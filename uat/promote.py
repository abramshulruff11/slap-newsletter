"""
Promote prompts between uat/prompts/ and prompts/ — with a diff and a confirm.

WHY THIS EXISTS
    uat/prompts/ is a FORK of prompts/ and promotion has always been manual
    copying. CLAUDE.md tracks the resulting drift as an open issue, and it is
    not theoretical: as of 2026-08-31 six of nine files differ, and they do not
    all lean the same way.

    The trap this tool exists to prevent: a naive "copy UAT over prod" would
    have destroyed 35 lines of rolling_feedback.txt, 27 of voice_examples.txt
    and 39 of pass2_writer.txt — real voice work that lives only in prod. So
    this refuses to delete content unless you say so explicitly.

WHAT IT DOES
    Classifies every prompt pair, then copies only what you confirm:

      identical   nothing to do
      eol-only    CRLF vs LF, content identical (see the .gitattributes item
                  in CLAUDE.md — this is noise, not drift)
      ahead       one side is a strict superset; promoting is purely additive
      DIVERGED    both sides have unique lines; copying either way LOSES work

    A copy that would remove lines from the destination is refused unless
    --allow-delete is passed. Every overwritten file is backed up to
    prompts/Archive/ first, matching the existing convention.

RUN
    python -X utf8 uat/promote.py                 # status only, changes nothing
    python -X utf8 uat/promote.py --diff          # status + full unified diffs
    python -X utf8 uat/promote.py --promote       # UAT -> prod, confirm each
    python -X utf8 uat/promote.py --backport      # prod -> UAT, confirm each
    python -X utf8 uat/promote.py --promote --file pass2_writer.txt
    python -X utf8 uat/promote.py --backport --yes --file voice_examples.txt

NOTE
    Prompts only. Promoting the meme/GIF libraries also means CODE changes in
    generate_newsletter.py (importing meme_library, the Pass 5 audits, the
    tweet budget). This tool reports that but cannot do it.
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UAT_DIR = Path(__file__).resolve().parent
REPO_ROOT = UAT_DIR.parent
UAT_PROMPTS = UAT_DIR / "prompts"
PROD_PROMPTS = REPO_ROOT / "prompts"
ARCHIVE = PROD_PROMPTS / "Archive"

# Prod-only files that are deliberately never in UAT.
PROD_ONLY_OK = {"base_prompt.txt", "meme_selector_index.txt"}

IDENTICAL, EOL_ONLY, UAT_AHEAD, PROD_AHEAD, DIVERGED, UAT_ONLY = (
    "identical", "eol-only", "uat-ahead", "prod-ahead", "diverged", "uat-only")


def _lines(path: Path) -> list:
    return path.read_text(encoding="utf-8").splitlines()


def classify(uat: Path, prod: Path) -> tuple:
    """(state, uat_only_lines, prod_only_lines)."""
    if not prod.exists():
        return UAT_ONLY, 0, 0
    u, p = _lines(uat), _lines(prod)
    if u == p:
        return IDENTICAL, 0, 0
    # Ignore trailing-CR noise before calling anything drift.
    us = [ln.rstrip("\r") for ln in u]
    ps = [ln.rstrip("\r") for ln in p]
    if us == ps:
        return EOL_ONLY, 0, 0
    uset, pset = set(filter(str.strip, us)), set(filter(str.strip, ps))
    only_u, only_p = len(uset - pset), len(pset - uset)
    if only_p == 0:
        return UAT_AHEAD, only_u, 0
    if only_u == 0:
        return PROD_AHEAD, 0, only_p
    return DIVERGED, only_u, only_p


def pairs(only: str | None) -> list:
    out = []
    for uf in sorted(UAT_PROMPTS.glob("*.txt")):
        if only and uf.name != only:
            continue
        out.append((uf, PROD_PROMPTS / uf.name))
    if not only:
        for pf in sorted(PROD_PROMPTS.glob("*.txt")):
            if not (UAT_PROMPTS / pf.name).exists() and pf.name not in PROD_ONLY_OK:
                out.append((UAT_PROMPTS / pf.name, pf))
    return out


def show_diff(src: Path, dst: Path, label: str) -> None:
    a = _lines(dst) if dst.exists() else []
    b = _lines(src)
    print(f"\n--- {label} ---")
    for line in difflib.unified_diff(a, b, str(dst), str(src), lineterm="", n=2):
        print("   " + line)


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = ARCHIVE / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


PLACEHOLDER_RE = __import__("re").compile(r"\{\{([A-Z0-9_]+)\}\}")

# Runners that must know how to substitute a placeholder before a prompt
# carrying one is safe to install.
RUNNERS = {"prompts": REPO_ROOT / "generate_newsletter.py",
           "prompts_uat": UAT_DIR / "generate_newsletter_uat.py"}


def unsupported_placeholders(src: Path, dst: Path) -> list:
    """
    Placeholders in `src` that the destination's runner cannot substitute.

    Caught the hard way: uat/prompts/gif_reference.txt carries
    {{GIF_LIBRARY_CATEGORIES}}, which uat/generate_newsletter_uat.py replaces
    at load time. generate_newsletter.py does not — so promoting that file
    alone would put a literal "{{GIF_LIBRARY_CATEGORIES}}" into the production
    writer prompt and the model would read raw template syntax.
    """
    tokens = set(PLACEHOLDER_RE.findall(src.read_text(encoding="utf-8")))
    if not tokens:
        return []
    runner = (RUNNERS["prompts_uat"] if dst.parent == UAT_PROMPTS
              else RUNNERS["prompts"])
    if not runner.exists():
        return sorted(tokens)
    code = runner.read_text(encoding="utf-8")
    return sorted(t for t in tokens if t not in code)


def confirm(prompt: str) -> bool:
    try:
        return input(f"  {prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        print("  (no tty — skipping; use --yes to copy non-interactively)")
        return False


STATE_NOTE = {
    IDENTICAL: "nothing to do",
    EOL_ONLY:  "line endings only — content identical, not real drift",
    UAT_AHEAD: "UAT is a strict superset; promoting is purely additive",
    PROD_AHEAD: "PROD is a strict superset; backporting is purely additive",
    DIVERGED:  "BOTH sides have unique lines — copying either way LOSES work",
    UAT_ONLY:  "exists only in UAT (no prod counterpart)",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote prompts between UAT and prod.")
    ap.add_argument("--promote", action="store_true", help="copy UAT -> prod")
    ap.add_argument("--backport", action="store_true", help="copy prod -> UAT")
    ap.add_argument("--file", help="operate on one filename only")
    ap.add_argument("--diff", action="store_true", help="print full unified diffs")
    ap.add_argument("--yes", action="store_true", help="skip per-file confirmation")
    ap.add_argument("--allow-delete", action="store_true",
                    help="permit a copy that REMOVES lines from the destination")
    args = ap.parse_args()

    if args.promote and args.backport:
        print("Pick one direction: --promote or --backport.")
        return 2
    direction = "promote" if args.promote else ("backport" if args.backport else None)

    rows = pairs(args.file)
    if not rows:
        print(f"No prompt named {args.file!r}.")
        return 1

    print("=" * 74)
    print(f"PROMPT STATUS — uat/prompts/  vs  prompts/"
          + (f"   [{direction} mode]" if direction else "   (status only)"))
    print("=" * 74)

    actionable = []
    for uat, prod in rows:
        state, only_u, only_p = classify(uat, prod) if uat.exists() else (PROD_AHEAD, 0, len(_lines(prod)))
        name = uat.name
        detail = ""
        if state in (UAT_AHEAD, PROD_AHEAD, DIVERGED):
            detail = f"  (+{only_u} uat-only / +{only_p} prod-only lines)"
        flag = "!!" if state == DIVERGED else "  "
        print(f" {flag} {state:<10} {name:<32}{detail}")
        if state not in (IDENTICAL, EOL_ONLY):
            actionable.append((uat, prod, state, only_u, only_p))
        if args.diff and state not in (IDENTICAL, EOL_ONLY):
            src, dst = (uat, prod) if direction != "backport" else (prod, uat)
            show_diff(src, dst, f"{name}  ({'uat->prod' if direction != 'backport' else 'prod->uat'})")

    print()
    for state in (DIVERGED, UAT_AHEAD, PROD_AHEAD, UAT_ONLY):
        if any(r[2] == state for r in actionable):
            print(f"  {state}: {STATE_NOTE[state]}")

    if not direction:
        print("\nStatus only — nothing was changed.")
        print("Re-run with --promote (UAT -> prod) or --backport (prod -> UAT).")
        print("Add --diff to see exactly what would move.")
        return 0

    print("\n" + "=" * 74)
    print(f"{'PROMOTING UAT -> PROD' if direction == 'promote' else 'BACKPORTING PROD -> UAT'}")
    print("=" * 74)

    copied = skipped = refused = 0
    for uat, prod, state, only_u, only_p in actionable:
        src, dst = (uat, prod) if direction == "promote" else (prod, uat)
        if not src.exists():
            print(f"\n  {src.name}: source missing — skipped")
            skipped += 1
            continue

        # Would this copy DELETE content from the destination?
        loses = only_p if direction == "promote" else only_u
        print(f"\n  {src.name}  [{state}]")
        if loses:
            print(f"    ⚠ this copy REMOVES {loses} line(s) that exist only in "
                  f"{dst.parent.name}/{dst.name}")
            if not args.allow_delete:
                print("    ✗ refused — rerun with --allow-delete if that is intended,")
                print("      or reconcile the two files by hand first.")
                refused += 1
                continue

        # A prompt carrying a placeholder the destination runner cannot
        # substitute would install literal template syntax. Always refuse.
        missing = unsupported_placeholders(src, dst)
        if missing:
            runner = ("generate_newsletter_uat.py" if dst.parent == UAT_PROMPTS
                      else "generate_newsletter.py")
            print(f"    ⚠ contains placeholder(s) {runner} cannot substitute: "
                  f"{', '.join('{{%s}}' % t for t in missing)}")
            print(f"    ✗ refused — copying this would put literal template syntax")
            print(f"      into the prompt. Add the substitution to {runner} first.")
            refused += 1
            continue

        if not args.yes and not confirm(f"copy {src.parent.name}/{src.name} -> {dst.parent.name}/?"):
            print("    skipped")
            skipped += 1
            continue

        b = backup(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print(f"    ✓ copied" + (f"  (backup: {b.relative_to(REPO_ROOT)})" if b else ""))

    print("\n" + "=" * 74)
    print(f"copied {copied} · skipped {skipped} · refused {refused}")
    if refused:
        print("\nRefused files have content on BOTH sides. Merge them by hand, or")
        print("pass --allow-delete once you are certain the losing side is stale.")
    if copied:
        print("\nPrompts only. If you are promoting the meme/GIF work, the CODE side")
        print("is NOT done: generate_newsletter.py still needs meme_library imported,")
        print("the Pass 5 audits, and the tweet budget. See CLAUDE.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
