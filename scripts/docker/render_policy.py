"""Render the ngrok Traffic Policy (Google login + email allowlist) for the Docker tunnel.

Refuses to write anything, so the tunnel never starts, unless NGROK_ALLOWED_EMAILS has at least one valid email.
Usage: python render_policy.py <output-path>
"""

import os
import re
import sys
from pathlib import Path

TEMPLATE = Path("/app/portal/ngrok/policy.template.yml")
EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]+$")


def main() -> int:
    emails = [e.strip().lower() for e in os.environ.get("NGROK_ALLOWED_EMAILS", "").split(",") if e.strip()]
    if not emails:
        print("NGROK_ALLOWED_EMAILS is empty in .env: refusing to expose the portal without a login allowlist.")
        return 1
    bad = [e for e in emails if not EMAIL.match(e)]
    if bad:
        print(f"Invalid email(s) in NGROK_ALLOWED_EMAILS: {', '.join(bad)}")
        return 1
    if not os.environ.get("NGROK_DOMAIN", "").strip():
        print("NGROK_DOMAIN is empty in .env.")
        return 1
    policy = TEMPLATE.read_text(encoding="utf-8").replace(
        "__ALLOWED_EMAILS__", ", ".join(f"'{e}'" for e in emails)
    )
    if "type: oauth" not in policy or "type: deny" not in policy or "__ALLOWED_EMAILS__" in policy:
        print("The policy template lost its login rules; refusing to continue.")
        return 1
    Path(sys.argv[1]).write_text(policy, encoding="utf-8")
    print(f"ngrok policy written: Google login required for {', '.join(emails)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
