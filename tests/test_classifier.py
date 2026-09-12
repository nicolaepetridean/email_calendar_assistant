"""Unit tests for the email classifier — mocks AzureOpenAI to avoid API calls."""

import json
import pytest
from unittest.mock import MagicMock

from agent.ai.classifier import Classifier, VALID_LABELS, ClassificationResult


def _make_classifier(label: str, is_urgent: bool = False, **extra) -> Classifier:
    """Return a Classifier backed by a mock that returns the given classification."""
    payload = {
        "label": label,
        "is_urgent": is_urgent,
        "summary": "Test summary.",
        "suggested_action": "Do something.",
        **extra,
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))]
    )
    return Classifier(mock_client)


class TestValidLabels:
    def test_contains_all_six_labels(self):
        assert VALID_LABELS == {
            "MEETING_REQUEST", "TASK", "INFO_ONLY",
            "SALES_OUTREACH", "MARKETING", "SPAM",
        }

    def test_is_immutable(self):
        assert isinstance(VALID_LABELS, frozenset)


class TestClassifier:
    def test_returns_correct_label(self):
        result = _make_classifier("TASK").classify("a@b.com", "Do this", "Body")
        assert result.label == "TASK"
        assert isinstance(result, ClassificationResult)

    def test_unknown_label_falls_back_to_info_only(self):
        result = _make_classifier("INVALID").classify("a@b.com", "Subj", "Body")
        assert result.label == "INFO_ONLY"

    def test_urgent_flag_propagated(self):
        result = _make_classifier("TASK", is_urgent=True).classify("a@b.com", "!", "Urgent!")
        assert result.is_urgent is True

    def test_meeting_fields_present_for_meeting_request(self):
        result = _make_classifier(
            "MEETING_REQUEST",
            meeting_duration_minutes=30,
            meeting_purpose="Q4 review",
        ).classify("a@b.com", "Schedule call", "Body")
        assert result.meeting_duration_minutes == 30
        assert result.meeting_purpose == "Q4 review"

    def test_body_passed_to_api(self):
        classifier = _make_classifier("INFO_ONLY")
        classifier.classify("a@b.com", "Subject", "Hello")
        call_args = classifier._client.chat.completions.create.call_args
        user_message = call_args.kwargs["messages"][1]["content"]
        assert "Hello" in user_message

    def test_long_body_is_truncated(self):
        classifier = _make_classifier("INFO_ONLY")
        long_body = "x" * 10_000
        classifier.classify("a@b.com", "Subject", long_body)
        call_args = classifier._client.chat.completions.create.call_args
        user_message = call_args.kwargs["messages"][1]["content"]
        # Body should be capped; the full 10k chars must not appear
        assert "x" * 10_000 not in user_message
