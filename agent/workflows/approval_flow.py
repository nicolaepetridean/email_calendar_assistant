import logging
import re
from datetime import datetime
from enum import Enum
from typing import Optional

from agent.google.calendar_client import CalendarClient, format_slots
from agent.ai.classifier import ClassificationResult
from agent.config import Config
from agent.storage.database import (
    PendingApproval,
    create_pending_approval,
    get_pending_by_thread,
    log_event,
    resolve_approval,
    update_approval_thread,
)
from agent.google.gmail_client import GmailClient
from agent.templates import render

logger = logging.getLogger(__name__)

APPROVAL_TAG = "[APPROVAL REQUEST]"


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sender_email(from_field: str) -> str:
    """Extract plain address from 'Display Name <addr>' format."""
    m = re.search(r"<([^>]+)>", from_field)
    return m.group(1) if m else from_field.strip()


def _parse_approval_reply(body: str) -> tuple[ApprovalDecision, int]:
    """Parse APPROVE [N] or REJECT from manager reply body."""
    upper = body.upper()
    m = re.search(r"\bAPPROVE\s+(\d+)\b", upper)
    if m:
        return ApprovalDecision.APPROVE, int(m.group(1))
    if re.search(r"\bAPPROVE\b", upper):
        return ApprovalDecision.APPROVE, 1
    if re.search(r"\bREJECT\b", upper):
        return ApprovalDecision.REJECT, 0
    return ApprovalDecision.UNKNOWN, 0


def _build_approval_email(
    original_from: str,
    original_subject: str,
    summary: str,
    slots: list[dict],
    approval_id: str,
    meeting_purpose: Optional[str],
) -> str:
    purpose_line = f"\n📋 PURPOSE: {meeting_purpose}" if meeting_purpose else ""
    return render(
        "approval_request",
        original_from=original_from,
        original_subject=original_subject,
        purpose_line=purpose_line,
        summary=summary,
        slots=format_slots(slots),
        approval_id=approval_id,
    )


# ---------------------------------------------------------------------------
# ApprovalFlow
# ---------------------------------------------------------------------------

class ApprovalFlow:
    def __init__(self, gmail: GmailClient, calendar: CalendarClient) -> None:
        self._gmail = gmail
        self._calendar = calendar

    def is_approval_response(self, email_data: dict) -> bool:
        """True if this email is a manager reply to one of our approval requests."""
        sender = _sender_email(email_data.get("from", ""))
        if sender.lower() != Config.MANAGER_EMAIL.lower():
            return False
        return get_pending_by_thread(email_data.get("thread_id", "")) is not None

    def create_approval(
        self,
        email_data: dict,
        classification: ClassificationResult,
        slots: list[dict],
    ) -> None:
        """Send an approval request email to the manager and persist the pending record."""
        serialisable_slots = [
            {"start": s["start"].isoformat(), "end": s["end"].isoformat()}
            for s in slots
        ]

        approval_id = create_pending_approval(
            original_email_id=email_data["id"],
            original_from=email_data["from"],
            original_subject=email_data["subject"],
            original_body=email_data.get("body", ""),
            original_message_id=email_data.get("message_id", ""),
            classification=classification.label,
            proposed_slots=serialisable_slots,
        )

        subject = f"{APPROVAL_TAG} Meeting request from {email_data['from']} — {email_data['subject']}"
        body = _build_approval_email(
            original_from=email_data["from"],
            original_subject=email_data["subject"],
            summary=classification.summary,
            slots=slots,
            approval_id=approval_id,
            meeting_purpose=classification.meeting_purpose,
        )

        try:
            _, thread_id = self._gmail.send_message(
                to=Config.MANAGER_EMAIL,
                subject=subject,
                body=body,
            )
        except Exception as exc:
            logger.error(f"Failed to send approval request to manager: {exc}")
            resolve_approval(approval_id, ApprovalDecision.REJECT)
            raise
        update_approval_thread(approval_id, thread_id)
        log_event(email_data["id"], "APPROVAL_REQUESTED", {
            "approval_id": approval_id,
            "manager": Config.MANAGER_EMAIL,
            "slots": serialisable_slots,
        })
        logger.info(f"Approval request sent to {Config.MANAGER_EMAIL} (id={approval_id})")

    def handle_approval_response(self, email_data: dict) -> None:
        """Dispatch manager reply to the appropriate approval handler."""
        approval = get_pending_by_thread(email_data["thread_id"])
        if not approval:
            logger.warning(f"No pending approval found for thread {email_data['thread_id']}")
            return

        body = email_data.get("body", "") or email_data.get("snippet", "")
        decision, slot_number = _parse_approval_reply(body)

        if decision == ApprovalDecision.APPROVE:
            self._execute_approval(approval, slot_number)
        elif decision == ApprovalDecision.REJECT:
            self._execute_rejection(approval)
        else:
            logger.warning(
                f"Could not parse APPROVE/REJECT from manager reply "
                f"(approval={approval.id}). Body: {body[:200]!r}"
            )

    # ------------------------------------------------------------------
    # Private handlers
    # ------------------------------------------------------------------

    def _execute_approval(self, approval: PendingApproval, slot_number: int) -> None:
        slots = approval.proposed_slots
        if slot_number < 1 or slot_number > len(slots):
            logger.warning(
                f"Manager replied APPROVE {slot_number} but only {len(slots)} slot(s) "
                f"were offered — defaulting to slot 1."
            )
            slot_number = 1
        chosen = slots[slot_number - 1]

        # Resolve first — prevents a duplicate event if the process crashes and restarts.
        resolve_approval(approval.id, ApprovalDecision.APPROVE)

        start_dt = datetime.fromisoformat(chosen["start"])
        end_dt = datetime.fromisoformat(chosen["end"])
        sender_email = _sender_email(approval.original_from)

        event_id = self._calendar.create_event(
            title=f"Meeting: {approval.original_subject}",
            start=start_dt,
            end=end_dt,
            attendee_email=sender_email,
            description=(
                f"Scheduled via AI Email Assistant.\n"
                f"Original request from: {approval.original_from}"
            ),
        )

        start_str = start_dt.strftime("%A, %B %d, %Y at %I:%M %p UTC")
        end_str = end_dt.strftime("%I:%M %p UTC")
        try:
            self._gmail.send_message(
                to=sender_email,
                subject=f"Re: {approval.original_subject}",
                body=render("meeting_confirmed", start_str=start_str, end_str=end_str),
                reply_to_message_id=approval.original_message_id,
            )
        except Exception as exc:
            # Event is already created; log the failure but don't crash — the
            # requester will see the calendar invite even without this confirmation.
            logger.error(f"Meeting created but failed to send confirmation to {sender_email}: {exc}")

        log_event(approval.original_email_id, "MEETING_CREATED", {
            "approval_id": approval.id,
            "event_id": event_id,
            "slot": chosen,
            "attendee": sender_email,
        })
        logger.info(f"Meeting created (approval={approval.id}, event={event_id})")

    def _execute_rejection(self, approval: PendingApproval) -> None:
        resolve_approval(approval.id, ApprovalDecision.REJECT)
        log_event(approval.original_email_id, "MEETING_REJECTED", {"approval_id": approval.id})
        logger.info(f"Meeting request rejected (approval={approval.id})")
