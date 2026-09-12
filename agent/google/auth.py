import logging
import os
import sys

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from agent.config import Config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/calendar",
]


def get_credentials() -> Credentials:
    """Load and, if expired, refresh Google OAuth credentials.

    Writes the refreshed token atomically to prevent file corruption on
    unexpected process termination mid-write.
    """
    try:
        creds = Credentials.from_authorized_user_file(Config.TOKEN_PATH, SCOPES)
    except FileNotFoundError:
        sys.exit(
            f"token.json not found at {Config.TOKEN_PATH!r}. "
            "Run auth.py to authenticate first."
        )

    if creds.expired and creds.refresh_token:
        try:
            logger.info("Refreshing expired Google credentials")
            creds.refresh(Request())
        except RefreshError as exc:
            sys.exit(
                f"Google credentials refresh failed: {exc}\n"
                "The token may have been revoked. Run auth.py to re-authenticate."
            )

        # Atomic write: temp file + os.replace prevents a corrupt token.json
        # if the process is killed mid-write.
        tmp_path = Config.TOKEN_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(creds.to_json())
        os.replace(tmp_path, Config.TOKEN_PATH)

    return creds
