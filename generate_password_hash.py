"""
Generate a bcrypt password hash to put into the USERS_JSON environment
variable. Run this once per client you want to grant access to.

Usage:
    python3 generate_password_hash.py

It will prompt for a username and password, then print the exact JSON
snippet to add to your .env file.
"""

import getpass
import json

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def main():
    username = input("Username for this client: ").strip()
    password = getpass.getpass("Password for this client: ")
    password_confirm = getpass.getpass("Confirm password: ")

    if password != password_confirm:
        print("Passwords did not match. Try again.")
        return

    if not username or not password:
        print("Username and password cannot be empty.")
        return

    hashed = hash_password(password)

    print("\nAdd this user to your USERS_JSON environment variable:\n")
    print(json.dumps({username: hashed}))
    print(
        "\nIf you already have other users, merge this key into the existing "
        "USERS_JSON object rather than replacing it, e.g.:\n"
        '  USERS_JSON=\'{"existing_client": "$2b$...", "%s": "%s"}\''
        % (username, hashed)
    )


if __name__ == "__main__":
    main()
