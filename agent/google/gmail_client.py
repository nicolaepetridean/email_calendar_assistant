import base64
import logging
import re
from email.mime.text import MIMEText
from email.header import decode_header as _decode_header
from typing import Optional

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from agent.config import Config

logger = logging.getLogger(__name__)

MAX_UNREAD_FETCH = 20   # kept low to stay within Gmail quota (250 units/s)


def _decode_str(value: str) -> str:
    parts = []
    for raw, charset in _decode_header(value):
        if isinstance(raw, bytes):
            parts.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(raw)
    return " ".join(parts)


def _strip_html(html: str) -> str:
    """Remove HTML tags and normalise whitespace.

    Lossy: block boundaries (div, p, br) become single spaces.
    """
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(payload: dict) -> str:
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            return _strip_html(html)

    parts = payload.get("parts", [])
    for part in parts:                          # prefer plain text
        if part.get("mimeType") == "text/plain":
            result = _extract_body(part)
            if result:
                return result
    for part in parts:                          # fall back to any part
        result = _extract_body(part)
        if result:
            return result

    return ""


class GmailClient:
    def __init__(self, credentials: Credentials) -> None:
        self._service = build("gmail", "v1", credentials=credentials)
        self._label_cache: dict[str, str] = {}

    def get_unread_inbox_messages(self) -> list[str]:
        result = (
            self._service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX", "UNREAD"], maxResults=MAX_UNREAD_FETCH)
            .execute(num_retries=Config.API_RETRIES)
        )
        return [m["id"] for m in result.get("messages", [])]

    def get_message(self, msg_id: str) -> dict:
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute(num_retries=Config.API_RETRIES)
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        return {
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": _decode_str(headers.get("subject", "(no subject)")),
            "message_id": headers.get("message-id", ""),
            "references": headers.get("references", ""),
            "in_reply_to": headers.get("in-reply-to", ""),
            "date": headers.get("date", ""),
            "body": _extract_body(msg.get("payload", {})),
            "snippet": msg.get("snippet", ""),
            "label_ids": msg.get("labelIds", []),
        }

    def ensure_label_exists(self, label_name: str) -> str:
        if label_name in self._label_cache:
            return self._label_cache[label_name]

        existing = (
            self._service.users().labels().list(userId="me")
            .execute(num_retries=Config.API_RETRIES)
        )
        for label in existing.get("labels", []):
            if label["name"] == label_name:
                self._label_cache[label_name] = label["id"]
                return label["id"]

        created = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute(num_retries=Config.API_RETRIES)
        )
        self._label_cache[label_name] = created["id"]
        logger.info(f"Created Gmail label: {label_name}")
        return created["id"]

    def apply_label(self, msg_id: str, label_name: str) -> None:
        label_id = self.ensure_label_exists(label_name)
        self._service.users().messages().modify(
            userId="me", id=msg_id, body={"addLabelIds": [label_id]}
        ).execute(num_retries=Config.API_RETRIES)

    def mark_as_read(self, msg_id: str) -> None:
        self._service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute(num_retries=Config.API_RETRIES)

    def archive(self, msg_id: str) -> None:
        self._service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["INBOX"]}
        ).execute(num_retries=Config.API_RETRIES)

    def flag_message(self, msg_id: str) -> None:
        self._service.users().messages().modify(
            userId="me", id=msg_id, body={"addLabelIds": ["STARRED"]}
        ).execute(num_retries=Config.API_RETRIES)

    def move_to_spam(self, msg_id: str) -> None:
        self._service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX", "UNREAD"]},
        ).execute(num_retries=Config.API_RETRIES)

    def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to_message_id: Optional[str] = None,
        reply_to_thread_id: Optional[str] = None,
        references: Optional[str] = None,
    ) -> tuple[str, str]:
        """Send an email and return (gmail_message_id, thread_id)."""
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject

        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            ref = f"{references} {reply_to_message_id}".strip() if references else reply_to_message_id
            msg["References"] = ref

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        send_body: dict = {"raw": raw}
        if reply_to_thread_id:
            send_body["threadId"] = reply_to_thread_id

        result = (
            self._service.users().messages().send(userId="me", body=send_body)
            .execute(num_retries=Config.API_RETRIES)
        )
        return result["id"], result["threadId"]
