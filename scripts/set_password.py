#!/usr/bin/env python3
"""Set the journal password.

Stores a salted scrypt hash in config.json (never plaintext) and ensures
a session-signing secret exists. Must match SCRYPT_KW in app.py.
"""
import getpass
import hashlib
import json
import secrets
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config.json"
# scrypt params — must match SCRYPT_KW in app.py
SCRYPT_KW = {"n": 2 ** 14, "r": 8, "p": 1}


def main() -> int:
    pw1 = getpass.getpass("Choose a password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if not pw1:
        print("Password must not be empty.", file=sys.stderr)
        return 1
    if pw1 != pw2:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pw1.encode("utf-8"), salt=salt, **SCRYPT_KW)

    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    cfg["password"] = f"{salt.hex()}${digest.hex()}"
    cfg.setdefault("secret", secrets.token_hex(32))

    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"Password set ({CONFIG}). Restart the server if it is running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
