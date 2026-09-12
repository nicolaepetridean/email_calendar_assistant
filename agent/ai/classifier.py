import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import AzureOpenAI

from agent.config import Config

logger = logging.getLogger(__name__)

# Single source of truth for the six primary email labels.
VALID_LABELS: frozenset[str] = frozenset({
    "MEETING_REQUEST",
    "TASK",
    "INFO_ONLY",
    "SALES_OUTREACH",
    "MARKETING",
    "SPAM",
})

# Keeps email bodies within a reasonable token budget for classification.
_BODY_TRUNCATION_CHARS = 3_000
_CLASSIFICATION_MAX_TOKENS = 512

_SYSTEM_PROMPT = """\
You are an email classification assistant for an AI Email & Calendar Agent.
Classify incoming emails and determine appropriate actions.

Primary labels (pick exactly one):
- MEETING_REQUEST: Asking to schedule, set up, or reschedule a meeting, call, or appointment
- TASK: Contains action items that require the manager to do something specific
- INFO_ONLY: FYI, notifications, status updates — no action needed
- SALES_OUTREACH: Unsolicited vendor or sales contact
- MARKETING: Newsletters, promotions, bulk marketing mail
- SPAM: Malicious, phishing, junk mail, or obvious spam

The URGENT modifier applies when there is a deadline, escalation, or time-critical matter.

Respond with valid JSON only — no explanation, no markdown fences."""

_USER_PROMPT = """\
Classify this email:

FROM: {from_addr}
SUBJECT: {subject}
BODY:
{body}

Return this JSON structure:
{{
  "label": "MEETING_REQUEST|TASK|INFO_ONLY|SALES_OUTREACH|MARKETING|SPAM",
  "is_urgent": false,
  "summary": "one concise sentence summarising the email",
  "suggested_action": "brief description of what the agent should do",
  "meeting_duration_minutes": 60,
  "meeting_purpose": "purpose / agenda of the requested meeting",
  "is_invoice": false,
  "auto_reply_body": null
}}

Rules:
- Omit meeting_duration_minutes and meeting_purpose for non-MEETING_REQUEST emails.
- is_invoice: true if the email is an invoice, receipt, or financial document.
- auto_reply_body: write a short, professional reply if a response is appropriate.
  SALES_OUTREACH → always write a polite, one-paragraph decline.
  TASK (non-urgent) → brief acknowledgement that the request was received.
  MARKETING, SPAM, MEETING_REQUEST → set to null (handled separately)."""


@dataclass
class ClassificationResult:
    label: str
    is_urgent: bool
    summary: str
    suggested_action: str
    meeting_duration_minutes: Optional[int] = None
    meeting_purpose: Optional[str] = None
    is_invoice: bool = False
    auto_reply_body: Optional[str] = None


class Classifier:
    """Classifies emails using Azure OpenAI. Constructed once and reused."""

    def __init__(self, client: AzureOpenAI) -> None:
        self._client = client

    def classify(self, from_addr: str, subject: str, body: str) -> ClassificationResult:
        def _esc(s: str) -> str:
            return s.replace("{", "{{").replace("}", "}}")

        prompt = _USER_PROMPT.format(
            from_addr=_esc(from_addr),
            subject=_esc(subject),
            body=_esc(body[:_BODY_TRUNCATION_CHARS]),
        )

        response = self._client.chat.completions.create(
            model=Config.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=_CLASSIFICATION_MAX_TOKENS,
            temperature=0,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if the model wraps the JSON
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:]).rstrip("`").strip()

        data = json.loads(raw)
        label = data.get("label", "INFO_ONLY")
        if label not in VALID_LABELS:
            logger.warning(f"Unknown label {label!r} from model — defaulting to INFO_ONLY")
            label = "INFO_ONLY"

        return ClassificationResult(
            label=label,
            is_urgent=bool(data.get("is_urgent", False)),
            summary=data.get("summary", ""),
            suggested_action=data.get("suggested_action", ""),
            meeting_duration_minutes=data.get("meeting_duration_minutes"),
            meeting_purpose=data.get("meeting_purpose"),
            is_invoice=bool(data.get("is_invoice", False)),
            auto_reply_body=data.get("auto_reply_body") or None,
        )
