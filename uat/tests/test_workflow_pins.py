"""
Run:  python -X utf8 uat/tests/test_workflow_pins.py

Locks SLA-74's follow-up to SLA-57: every `pip install` in every workflow
installs exactly the version requirements.txt pins.

WHY
    SLA-57 pinned requirements.txt to what a green run installed, so a package
    can only change under us through a deliberate bump. But three workflows do
    not install from requirements.txt -- they pip-install a short list by name,
    because they only need the Substack client -- and those lists were left
    unpinned. publish-substack.yml, the job that actually publishes the issue at
    noon, installed whatever curl_cffi, python-dotenv and requests were newest
    that day. The pin covered the morning run and skipped the one that ships.

WHAT THIS LOCKS
    Every package named on a `pip install` line in .github/workflows/ must:
      1. carry an exact `==` version, and
      2. be a package requirements.txt pins, at the SAME version.
    `pip install -r requirements.txt` is the pinned file itself, so it passes.
    To bump a version, change requirements.txt and these lines in one commit;
    this test fails until they agree.

0 network, 0 API calls.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
failures: list[str] = []


def check_true(label, cond):
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}")
    if not cond:
        failures.append(label)


def canon(name: str) -> str:
    """PEP 503 name normalisation: curl_cffi == curl-cffi == Curl.CFFI."""
    return re.sub(r"[-_.]+", "-", name).lower()


# name[extras]==version. Extras (psycopg[binary], SLA-65) select optional
# parts of the SAME package at the SAME pinned version, so they are still an
# exact pin; the name the pin is keyed by is the one without them.
PIN = r"([A-Za-z0-9_.\-]+)(?:\[[A-Za-z0-9_,.\-]+\])?==(\S+)"

pins: dict[str, str] = {}
for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    m = re.fullmatch(PIN, line)
    check_true(f"requirements.txt: {line!r} is an exact pin", bool(m))
    if m:
        pins[canon(m.group(1))] = m.group(2)

print()
installs = 0
for wf in sorted((REPO / ".github" / "workflows").glob("*.yml")):
    for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
        m = re.search(r"\bpip install\s+(.+)$", line)
        if not m:
            continue
        installs += 1
        args = m.group(1).split()
        where = f"{wf.name}:{n}"
        if args[:2] == ["-r", "requirements.txt"]:
            check_true(f"{where}: installs requirements.txt", True)
            continue
        for arg in args:
            if arg.startswith("-"):
                check_true(f"{where}: no unexpected pip flag {arg!r}", False)
                continue
            pm = re.fullmatch(PIN, arg)
            if not pm:
                check_true(f"{where}: {arg!r} has an exact == version", False)
                continue
            want = pins.get(canon(pm.group(1)))
            check_true(f"{where}: {arg} matches requirements.txt "
                       f"({pm.group(1)}=={want})", want == pm.group(2))

check_true("found the workflows' pip installs at all (the scan is not empty)",
           installs >= 4)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("All workflow pip installs match requirements.txt.")
