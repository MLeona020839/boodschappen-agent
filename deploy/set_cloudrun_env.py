"""Write deploy/cloudrun.env.yaml by asking for each secret (input is hidden).

    .venv/bin/python deploy/set_cloudrun_env.py

Values are never printed. Press Enter to keep a value already in the file, or, for the API key,
to use ANTHROPIC_API_KEY from your shell.
"""
from __future__ import annotations

import getpass
import json
import os
import re
from pathlib import Path

PATH = Path(__file__).with_name("cloudrun.env.yaml")
TEMPLATE = Path(__file__).with_name("cloudrun.env.example.yaml")
PLACEHOLDERS = {"sk-ant-...", "choose-something-long",
                "postgresql://postgres.xxxx:password@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"}

QUESTIONS = [
    ("ANTHROPIC_API_KEY", "Anthropic API key (starts with sk-ant-)", lambda v: v.startswith("sk-ant-")),
    ("ADMIN_CODE", "Admin code for the owner page (at least 8 characters)", lambda v: len(v) >= 8),
    ("PROMOS_DATABASE_URL", "Supabase connection string (starts with postgresql://)",
     lambda v: v.startswith(("postgresql://", "postgres://"))),
]


def read_existing() -> dict[str, str]:
    text = (PATH if PATH.exists() else TEMPLATE).read_text()
    values = {}
    for line in text.splitlines():
        m = re.match(r'^([A-Z_]+):\s*"(.*)"\s*$', line)
        if m:
            values[m.group(1)] = json.loads(f'"{m.group(2)}"')
    return values


def main() -> None:
    values = read_existing()
    for key, prompt, valid in QUESTIONS:
        current = values.get(key, "")
        have = current and current not in PLACEHOLDERS
        fallback = os.environ.get("ANTHROPIC_API_KEY", "") if key == "ANTHROPIC_API_KEY" and not have else ""
        hint = " [Enter = keep current]" if have else " [Enter = use the key from your shell]" if fallback else ""
        while True:
            value = getpass.getpass(f"{prompt}{hint}: ").strip()
            value = value or (current if have else fallback)
            if value and valid(value):
                values[key] = value
                break
            print("  That doesn't look right; try again (nothing was saved).")
    lines = [
        "# Written by deploy/set_cloudrun_env.py. Git-ignored; never commit this file.",
        *(f"{k}: {json.dumps(v)}" for k, v in values.items()),
    ]
    PATH.write_text("\n".join(lines) + "\n")
    PATH.chmod(0o600)
    print(f"Saved {PATH} ({len(values)} settings). Values are not shown.")


if __name__ == "__main__":
    main()
