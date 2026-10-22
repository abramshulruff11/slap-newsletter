"""Local check: does this Substack cookie string log in? Prints pass/fail only.

Run it on your own machine BEFORE updating the SUBSTACK_COOKIES_STRING secret:

    python -X utf8 substack_poc/check_cookie.py

The cookie string is read with a hidden prompt (nothing echoed, nothing in shell
history) and is never printed. Only the cookie NAMES and value lengths are shown,
so a truncated or misnamed cookie is visible without exposing it.

A pass here but a 401 in GitHub means the session only works from the machine that
created it (IP/device binding). A fail here means the cookie itself is bad.
"""
import getpass
import sys


def main() -> int:
    from substack import Api

    raw = getpass.getpass("Paste cookie string (hidden), then Enter: ").strip()
    if not raw:
        print("Nothing entered.")
        return 2

    parsed = Api._parse_cookies_string(raw)
    print(f"Parsed {len(parsed)} cookie(s):")
    for name, value in parsed.items():
        print(f"  {name}: {len(value)} characters")
    if "substack.sid" not in parsed:
        print("WARNING: no cookie named exactly 'substack.sid' in the string.")

    try:
        api = Api(cookies_string=raw)
        profile = api.get_user_profile()
    except Exception as e:  # noqa: BLE001
        print(f"\nFAIL: {type(e).__name__}: {str(e)[:200]}")
        return 1

    print(f"\nPASS: signed in as user id {profile.get('id')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
