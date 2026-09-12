import logging
import re

from agent.workflows.approval_flow import ApprovalFlow
from agent.google.calendar_client import CalendarClient
from agent.ai.classifier import Classifier, ClassificationResult
from agent.config import Config
from agent.storage.database import log_event, mark_processed
from agent.google.gmail_client import GmailClient
from agent.templates import render

logger = logging.getLogger(__name__)


def _sender_email(from_field: str) -> str:
    m = re.search(r"<([^>]+)>", from_field)
    return m.group(1) if m else from_field.strip()


class EmailOrchestrator:
    """Drives the full classification → label → action pipeline for each email.

    The polling loop in main.py is responsible for *fetching* messages.
    This class is responsible for *processing* them — keeping the two
    concerns separate and the orchestrator independently testable.
    """

    def __init__(
        self,
        gmail: GmailClient,
        calendar: CalendarClient,
        classifier: Classifier,
        approval: ApprovalFlow,
    ) -> None:
        self.gmail = gmail          # public — used by the polling loop for fetch/reconnect
        self._calendar = calendar
        self._classifier = classifier
        self._approval = approval

    def process(self, email_data: dict) -> None:
        """Process one email end-to-end. Idempotent — safe to retry."""
        email_id = email_data["id"]

        if self._approval.is_approval_response(email_data):
            logger.info(f"[{email_id}] Approval response — processing")
            self._approval.handle_approval_response(email_data)
            self.gmail.mark_as_read(email_id)
            mark_processed(email_id, "APPROVAL_RESPONSE", False, "Approval response", "executed")
            return

        body = email_data.get("body") or email_data.get("snippet", "")
        result = self._classifier.classify(email_data["from"], email_data["subject"], body)

        logger.info(
            f"[{email_id}] label={result.label} urgent={result.is_urgent}"
            f" — {result.summary[:80]}"
        )

        self.gmail.apply_label(email_id, result.label)
        if result.is_urgent:
            self.gmail.apply_label(email_id, "URGENT")

        action_taken = self._take_action(email_id, email_data, result)

        self.gmail.mark_as_read(email_id)
        mark_processed(email_id, result.label, result.is_urgent, result.summary, action_taken)
        log_event(email_id, "EMAIL_PROCESSED", {
            "from": email_data["from"],
            "subject": email_data["subject"],
            "label": result.label,
            "is_urgent": result.is_urgent,
            "summary": result.summary,
            "action_taken": action_taken,
        })

    # ------------------------------------------------------------------
    # Private action handlers
    # ------------------------------------------------------------------

    def _take_action(
        self,
        email_id: str,
        email_data: dict,
        result: ClassificationResult,
    ) -> str:
        """Dispatch to label-specific logic and return the action_taken string."""
        if result.label == "MEETING_REQUEST":
            return self._handle_meeting_request(email_id, email_data, result)
        if result.label == "SPAM":
            self.gmail.move_to_spam(email_id)
            return "moved_to_spam"
        if result.label == "MARKETING":
            self.gmail.archive(email_id)
            return "archived"
        if result.label == "SALES_OUTREACH":
            return self._handle_sales_outreach(email_id, email_data, result)
        if result.label == "INFO_ONLY":
            return self._handle_info_only(email_id, email_data, result)
        if result.label == "TASK":
            return self._handle_task(email_id, email_data, result)
        return "labeled"

    def _handle_meeting_request(
        self,
        email_id: str,
        email_data: dict,
        result: ClassificationResult,
    ) -> str:
        duration = result.meeting_duration_minutes or Config.DEFAULT_MEETING_DURATION
        slots = self._calendar.find_free_slots(
            duration, Config.MEETING_SLOT_COUNT, Config.MEETING_LOOKAHEAD_DAYS
        )
        if not slots:
            logger.warning(f"[{email_id}] No free calendar slots found")
            return "no_slots_found"
        self._approval.create_approval(email_data, result, slots)
        return "approval_requested"

    def _handle_sales_outreach(
        self,
        email_id: str,
        email_data: dict,
        result: ClassificationResult,
    ) -> str:
        if Config.AUTO_DECLINE_SALES and result.auto_reply_body:
            self._send_auto_reply(email_data, result.auto_reply_body)
            self.gmail.archive(email_id)
            return "decline_sent_archived"
        self.gmail.archive(email_id)
        return "archived"

    def _handle_info_only(
        self,
        email_id: str,
        email_data: dict,
        result: ClassificationResult,
    ) -> str:
        if result.is_invoice:
            self.gmail.apply_label(email_id, "Finance")
            self.gmail.archive(email_id)
            return "archived_to_finance"
        self.gmail.archive(email_id)
        return "archived"

    def _handle_task(
        self,
        email_id: str,
        email_data: dict,
        result: ClassificationResult,
    ) -> str:
        if result.is_urgent:
            self.gmail.flag_message(email_id)
            self._notify_manager_urgent(email_data, result)
            return "flagged_manager_notified"
        if result.auto_reply_body:
            self._send_auto_reply(email_data, result.auto_reply_body)
            return "auto_replied"
        return "labeled"

    def _send_auto_reply(self, email_data: dict, body: str) -> None:
        self.gmail.send_message(
            to=_sender_email(email_data["from"]),
            subject=f"Re: {email_data['subject']}",
            body=body,
            reply_to_message_id=email_data.get("message_id"),
        )

    def _notify_manager_urgent(self, email_data: dict, result: ClassificationResult) -> None:
        self.gmail.send_message(
            to=Config.MANAGER_EMAIL,
            subject=f"[URGENT] {result.summary[:60]}",
            body=render(
                "urgent_notification",
                sender=email_data["from"],
                subject=email_data["subject"],
                summary=result.summary,
                suggested_action=result.suggested_action,
            ),
        )
