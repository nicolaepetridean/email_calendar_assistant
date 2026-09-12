"""Unit tests for approval reply parsing — the highest-risk logic to change."""

import pytest
from agent.workflows.approval_flow import _parse_approval_reply, ApprovalDecision


@pytest.mark.parametrize("body, expected_decision, expected_slot", [
    ("APPROVE 1",               ApprovalDecision.APPROVE,  1),
    ("APPROVE 2",               ApprovalDecision.APPROVE,  2),
    ("APPROVE 3",               ApprovalDecision.APPROVE,  3),
    ("approve 2",               ApprovalDecision.APPROVE,  2),
    ("APPROVE",                 ApprovalDecision.APPROVE,  1),  # defaults to slot 1
    ("Yes, APPROVE 1 please",   ApprovalDecision.APPROVE,  1),
    ("REJECT",                  ApprovalDecision.REJECT,   0),
    ("reject this one",         ApprovalDecision.REJECT,   0),
    ("I'm not sure",            ApprovalDecision.UNKNOWN,  0),
    ("",                        ApprovalDecision.UNKNOWN,  0),
])
def test_parse_approval_reply(body, expected_decision, expected_slot):
    decision, slot = _parse_approval_reply(body)
    assert decision == expected_decision
    assert slot == expected_slot
