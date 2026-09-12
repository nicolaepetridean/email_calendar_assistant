#!/usr/bin/env python3
"""
Run this script ONCE on your local machine to authenticate with Google.
It will open a browser window for OAuth consent and save token.json.

Usage:
    CREDENTIALS_PATH=credentials/credentials.json \
    TOKEN_PATH=credentials/token.json \
    python auth.py
"""

import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/calendar",
]

CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials/credentials.json")
TOKEN_PATH = os.getenv("TOKEN_PATH", "credentials/token.json")


def main():
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"ERROR: credentials.json not found at {CREDENTIALS_PATH}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials")
        sys.exit(1)

    print(f"Opening browser for Google OAuth consent...")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"\nSuccess! Token saved to: {TOKEN_PATH}")
    print("You can now start the agent with: docker compose up")


if __name__ == "__main__":
    main()
