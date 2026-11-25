"""Emergency password reset for a locked-out account (run on the PC that hosts the database).

Usage:  .venv\\Scripts\\python.exe tools\\reset_password.py [username] [--data-dir PATH]

The new password is typed at a hidden prompt; it is never echoed or written anywhere except as a hash.
Close the app first if it is running so it doesn't keep a stale session.
"""
from __future__ import annotations

import argparse
import getpass
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths  # noqa: E402
from core.authentication import hash_password  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a School Paging System account password.")
    parser.add_argument("username", nargs="?", help="account to reset (lists accounts if omitted)")
    parser.add_argument("--data-dir", help="data folder, if not the default")
    args = parser.parse_args()
    if args.data_dir:
        paths.set_data_dir(args.data_dir)

    db = paths.database_path()
    if not db.exists():
        print(f"No database found at {db}")
        return 1
    con = sqlite3.connect(db)
    users = con.execute("SELECT u.username, u.full_name, r.name, u.is_active FROM users u "
                        "LEFT JOIN roles r ON r.id = u.role_id ORDER BY u.id").fetchall()
    if not args.username:
        print(f"Accounts in {db}:")
        for username, full_name, role, active in users:
            print(f"  {username:<20} {full_name}  [{role}]{'' if active else '  (disabled)'}")
        print("\nRun again with a username to reset its password.")
        return 0
    if args.username not in {u[0] for u in users}:
        print(f"No account named '{args.username}'.")
        return 1

    password = getpass.getpass(f"New password for {args.username}: ")
    problems = [msg for ok, msg in ((len(password) >= 8, "at least 8 characters"),
                                    (re.search(r"[A-Za-z]", password), "a letter"),
                                    (re.search(r"\d", password), "a number")) if not ok]
    if problems:
        print("Password needs " + ", ".join(problems) + ".")
        return 1
    if getpass.getpass("Repeat it: ") != password:
        print("Passwords didn't match.")
        return 1

    with con:
        con.execute("UPDATE users SET password_hash = ?, failed_attempts = 0, locked_until = NULL, is_active = 1 "
                    "WHERE username = ?", (hash_password(password), args.username))
    print(f"Password for '{args.username}' reset. You can sign in now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
