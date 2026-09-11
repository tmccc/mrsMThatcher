"""Validation details remain useful and bounded in operator-facing reports."""

import mrs_log_digest as digest
from tests.helpers.digest_records import structured_record


def _failed(offset, **fields):
    return structured_record(offset, {
        "event": "single_call_reply_decision", "lane": "quote-tweet",
        "target_id": str(100 + offset), "pipeline_status": "operational_failure",
        "outcome_type": "operational", "local_validation_status": "failed",
        "error_category": "local_validation", "failure_reason": "invalid_model_response",
        "model_call_count": 1, "provider_request_attempt_count": 1, **fields,
    })


def test_markdown_explains_rules_and_missing_or_redacted_details():
    records = [
        _failed(0, validation_error_codes=["reply_contains_mention", "reply_contains_link_or_address"]),
        _failed(1),
        _failed(2, validation_error_codes=[]),
        _failed(3, validation_error_codes=["reply_contains_emoji", "PRIVATE REJECTED REPLY"]),
        _failed(4, validation_error_codes="PRIVATE REJECTED REPLY"),
    ]
    report = digest.analyse(records)
    rendered = digest.render_markdown(report)
    assert "### Rejected reply validation details" in rendered
    assert "reply_contains_link_or_address, reply_contains_mention" in rendered
    assert "unavailable (older event did not record rules)" in rendered
    assert "unavailable (no rule codes recorded)" in rendered
    assert "partial (some values omitted); 1 values omitted" in rendered
    assert "unavailable (malformed rule details); 1 values omitted" in rendered
    assert "| quote-tweet | 100 | local_validation |" in rendered
    assert "PRIVATE REJECTED REPLY" not in rendered


def test_markdown_caps_candidate_rows_and_retains_total_rule_counts():
    rendered = digest.render_markdown(digest.analyse([
        _failed(i, validation_error_codes=["reply_contains_mention"])
        for i in range(45)
    ]))
    assert "reply_contains_mention=45" in rendered
    assert "Earlier rejected decisions omitted from this table: **5**" in rendered
    assert "| quote-tweet | 100 | local_validation |" not in rendered
    assert "| quote-tweet | 144 | local_validation |" in rendered


def test_success_only_report_has_no_rejection_table():
    assert "### Rejected reply validation details" not in digest.render_markdown(digest.analyse([]))
