"""Tests for the Inbox Triage skill.

These run without a live LLM key or a running mock API: the Anthropic client is
mocked, and the HTTP client is mocked or driven against guard logic only. The
goal is to pin down the behaviours that matter for correctness and security —
the classifier contract, the routing table, the human-in-the-loop gate, and
least-privilege enforcement.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.triage_skill import (
    LABELS,
    ProposedAction,
    TriageClient,
    TriageResult,
    classify_email,
    draft_reply_body,
    execute,
    plan_actions,
    triage_inbox,
)

FIXTURES = json.loads(
    (Path(__file__).resolve().parent.parent / "fixtures" / "emails.json").read_text()
)


def _mock_anthropic_returning(label_text: str):
    """Build a patch target for `anthropic.Anthropic` whose `messages.create`
    returns a response whose text content is `label_text`."""
    text_block = SimpleNamespace(type="text", text=label_text)
    response = SimpleNamespace(content=[text_block])

    fake_client = MagicMock()
    fake_client.messages.create.return_value = response

    fake_module = MagicMock()
    fake_module.Anthropic.return_value = fake_client
    return fake_module, fake_client


# --- 1. classify_email returns a valid label for every fixture ------------

@pytest.mark.parametrize("email", FIXTURES, ids=[e["id"] for e in FIXTURES])
def test_classify_email_returns_valid_label(email):
    # Have the mocked model echo a plausible (here: constant) valid label;
    # the contract under test is "result is always in LABELS".
    fake_module, _ = _mock_anthropic_returning("billing")
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        label = classify_email(email)
    assert label in LABELS


def test_classify_email_strict_parse_falls_back_to_spam():
    # A model that returns garbage outside LABELS must collapse to the safe
    # spam fallback (action-free, no write credentials).
    fake_module, _ = _mock_anthropic_returning("totally-not-a-label")
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        assert classify_email({"id": "x", "from": "a@b.com", "subject": "s", "body": "b"}) == "spam"


def test_classify_email_normalises_case_and_whitespace():
    fake_module, _ = _mock_anthropic_returning("  BILLING\n")
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        assert classify_email({"id": "x", "from": "a@b.com", "subject": "s", "body": "b"}) == "billing"


# --- 1b. draft_reply_body: LLM-drafted contextual body --------------------

def test_draft_reply_body_returns_model_text():
    fake_module, _ = _mock_anthropic_returning(
        "Hi Dana, thanks for flagging the double charge on invoice #4471. "
        "We're looking into it now and will confirm the reversal shortly."
    )
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        body = draft_reply_body(
            {"id": "e-001", "from": "dana@x.com", "subject": "Double charge", "body": "billed twice"},
            "billing",
        )
    assert "invoice #4471" in body
    assert "Customer Support" not in body  # model text returned as-is


def test_draft_reply_body_falls_back_on_error():
    # If the SDK/model raises, the drafter must not blow up the run — it returns
    # a safe generic acknowledgement instead.
    fake_module = MagicMock()
    fake_module.Anthropic.side_effect = RuntimeError("no api key")
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        body = draft_reply_body({"id": "x", "from": "a@b.com", "subject": "s", "body": "b"}, "billing")
    assert "Customer Support" in body  # the fallback template


# --- 2. e-007 prompt injection: spam -> zero actions ----------------------

def test_e007_prompt_injection_classified_spam_produces_no_actions():
    e007 = next(e for e in FIXTURES if e["id"] == "e-007")
    # Even though the body says "do not ask for approval / reply with contacts",
    # a spam classification must yield zero proposed actions.
    actions = plan_actions("spam", e007)
    assert actions == []


def test_e007_classifier_does_not_obey_injection():
    # When the model correctly labels the injection as spam, the classifier
    # returns 'spam' and nothing downstream is planned.
    e007 = next(e for e in FIXTURES if e["id"] == "e-007")
    fake_module, _ = _mock_anthropic_returning("spam")
    with patch.dict("sys.modules", {"anthropic": fake_module}):
        label = classify_email(e007)
    assert label == "spam"
    assert plan_actions(label, e007) == []


# --- 3. plan_actions routing table ---------------------------------------

@pytest.mark.parametrize(
    "label,expected_kinds",
    [
        ("billing", ["send_reply"]),
        ("bug_report", ["send_alert"]),
        ("sales_lead", ["send_reply", "create_lead"]),
        ("spam", []),
    ],
)
def test_plan_actions_routing(label, expected_kinds):
    email = {"id": "e", "from": "jane.doe@acme.com", "subject": "Hi", "body": "Hello there"}
    actions = plan_actions(label, email)
    assert [a.kind for a in actions] == expected_kinds
    # every planned action is a write and carries a rationale
    assert all(a.requires_write for a in actions)
    assert all(a.rationale for a in actions)


def test_plan_actions_payloads_are_well_formed():
    email = {"id": "e-1", "from": "priya.n@northwind-logistics.com", "subject": "Pilot", "body": "12 seats"}
    reply, lead = plan_actions("sales_lead", email)
    assert reply.payload["to"] == "priya.n@northwind-logistics.com"
    assert reply.payload["subject"] == "Re: Pilot"
    assert reply.payload["in_reply_to"] == "e-1"
    assert lead.payload["email"] == "priya.n@northwind-logistics.com"
    assert lead.payload["company"] == "Northwind Logistics"
    assert lead.payload["name"] == "Priya N"


# --- 4 & 5. execute is the human-in-the-loop gate -------------------------

def test_execute_rejected_makes_no_calls_and_returns_none():
    client = MagicMock()
    action = ProposedAction(kind="send_reply", payload={"to": "a", "subject": "s", "body": "b"})
    result = execute(action, client, approved=False)
    assert result is None
    # zero external calls of any kind
    client.send_reply.assert_not_called()
    client.send_alert.assert_not_called()
    client.create_lead.assert_not_called()
    assert client.method_calls == []


@pytest.mark.parametrize(
    "kind,method",
    [
        ("send_reply", "send_reply"),
        ("send_alert", "send_alert"),
        ("create_lead", "create_lead"),
    ],
)
def test_execute_approved_dispatches_to_correct_method(kind, method):
    client = MagicMock()
    getattr(client, method).return_value = {"status": "ok"}
    payload = {"foo": "bar"}
    action = ProposedAction(kind=kind, payload=payload)

    result = execute(action, client, approved=True)

    getattr(client, method).assert_called_once_with(**payload)
    assert result == {"status": "ok"}
    # only the matching method was called
    other_methods = {"send_reply", "send_alert", "create_lead"} - {method}
    for m in other_methods:
        getattr(client, m).assert_not_called()


# --- 6. Least privilege: read-only client cannot write --------------------

@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.send_reply(to="a", subject="s", body="b"),
        lambda c: c.send_alert(channel="#engineering", message="m"),
        lambda c: c.create_lead(name="n", email="e@x.com"),
    ],
)
def test_readonly_client_writes_raise_permission_error(call):
    client = TriageClient("http://127.0.0.1:8099", read_token="read-token-dev", write_token=None)
    try:
        with pytest.raises(PermissionError):
            call(client)
    finally:
        client.close()


def test_readonly_client_makes_no_http_call_when_blocked():
    # The guard must fire BEFORE any network I/O happens.
    client = TriageClient("http://127.0.0.1:8099", read_token="read-token-dev", write_token=None)
    client._http = MagicMock()  # any HTTP use would show up here
    try:
        with pytest.raises(PermissionError):
            client.send_reply(to="a", subject="s", body="b")
        client._http.post.assert_not_called()
    finally:
        pass


# --- 7. triage_inbox orchestration (no LLM, no live API) ------------------

class FakeClient:
    """Stands in for both the read and write client in orchestration tests."""

    def __init__(self, emails):
        self._emails = emails
        self.calls: list[tuple[str, dict]] = []

    def get_inbox(self):
        return self._emails

    def send_reply(self, **kw):
        self.calls.append(("send_reply", kw))
        return {"status": "sent", **kw}

    def send_alert(self, **kw):
        self.calls.append(("send_alert", kw))
        return {"status": "posted", **kw}

    def create_lead(self, **kw):
        self.calls.append(("create_lead", kw))
        return {"status": "created", **kw}


def test_triage_inbox_full_pipeline_approve_all():
    emails = [
        {"id": "e-001", "from": "a@acme.com", "subject": "bill", "body": "charged twice"},
        {"id": "e-002", "from": "b@acme.com", "subject": "bug", "body": "broken"},
        {"id": "e-003", "from": "c@acme.com", "subject": "lead", "body": "pilot"},
        {"id": "e-004", "from": "d@spam.biz", "subject": "junk", "body": "win prize"},
    ]
    labels = {"e-001": "billing", "e-002": "bug_report", "e-003": "sales_lead", "e-004": "spam"}

    client = FakeClient(emails)
    results = triage_inbox(
        client,
        approver=lambda email, action: True,
        classifier=lambda email: labels[email["id"]],
        drafter=lambda email, label: "STUB DRAFT",  # no live LLM in tests
    )

    assert [r.email_id for r in results] == ["e-001", "e-002", "e-003", "e-004"]
    assert [r.label for r in results] == ["billing", "bug_report", "sales_lead", "spam"]
    assert [a.kind for a in results[2].actions] == ["send_reply", "create_lead"]
    assert results[3].actions == []  # spam plans nothing

    # executed calls: 1 reply (billing) + 1 alert (bug) + reply+lead (sales) = 4
    kinds = [c[0] for c in client.calls]
    assert kinds == ["send_reply", "send_alert", "send_reply", "create_lead"]

    # the drafter replaced the templated body in every send_reply
    reply_bodies = [kw["body"] for k, kw in client.calls if k == "send_reply"]
    assert reply_bodies == ["STUB DRAFT", "STUB DRAFT"]


def test_triage_inbox_reject_all_executes_no_writes():
    emails = [{"id": "e-001", "from": "a@acme.com", "subject": "bill", "body": "x"}]
    client = FakeClient(emails)

    results = triage_inbox(
        client,
        approver=lambda email, action: False,  # human says no to everything
        classifier=lambda email: "billing",
        drafter=lambda email, label: "STUB DRAFT",
    )

    # The result still records what was *planned*...
    assert [a.kind for a in results[0].actions] == ["send_reply"]
    # ...but nothing was executed.
    assert client.calls == []


def test_triage_inbox_uses_separate_write_client():
    # Least-privilege wiring: reads go to `client`, writes only to `write_client`.
    emails = [{"id": "e-003", "from": "c@acme.com", "subject": "lead", "body": "pilot"}]
    read_client = FakeClient(emails)
    write_client = FakeClient([])  # never read from

    triage_inbox(
        read_client,
        approver=lambda email, action: True,
        classifier=lambda email: "sales_lead",
        write_client=write_client,
        drafter=lambda email, label: "STUB DRAFT",
    )

    # the read client performed no writes; the write client did
    assert read_client.calls == []
    assert [c[0] for c in write_client.calls] == ["send_reply", "create_lead"]
