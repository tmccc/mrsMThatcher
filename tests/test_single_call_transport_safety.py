"""Offline root-adapter regressions for provider cooldown and image transport."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.reply_fixtures import patch_reply_context_method, patch_reply_owner_method
from tests.helpers.single_call_fixtures import FakeHttpResponse, FakeRepository, context, enabled_config, raw_decision, response_envelope, valid_png
from tests.test_single_call_conversation_contract import NATURAL_REPLIES


def configure(monkeypatch, responses):
    """Bind the root transport to local doubles and a deterministic clock."""
    calls = []
    sleeps = []
    saves = []

    def post(*args, **kwargs):
        """Consume only the explicitly configured local response list."""
        calls.append(kwargs)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(bot, 'single_call_reply', enabled_config())
    monkeypatch.setattr(bot, 'now_epoch', lambda: 2_000_000_000)
    patch_reply_owner_method(
        monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _: [],
    )
    monkeypatch.setattr(bot, 'reply_evidence_repository', FakeRepository)
    monkeypatch.setattr(bot, 'require_remote_operation_unpaused', lambda *_: None)
    monkeypatch.setattr(bot, 'report_bot_health_progress', lambda *_: None)
    monkeypatch.setattr(bot, 'sleep', sleeps.append)
    monkeypatch.setattr(bot.requests, 'post', post)
    monkeypatch.setattr(bot, 'save_state', lambda state, **_: saves.append(copy.deepcopy(state)))
    return calls, sleeps, saves


@pytest.mark.parametrize('kind,text', NATURAL_REPLIES)
def test_real_root_adapter_accepts_natural_conversation_in_one_call(monkeypatch, kind, text):
    """A mocked editorial decision reaches a bound draft without another call."""
    response = response_envelope(raw_decision(kind=kind, reply=text))
    calls, sleeps, _ = configure(monkeypatch, [FakeHttpResponse(200, body=response)])
    result = bot.evaluate_single_call_reply(context(), state=bot.default_state())
    assert result.status == 'reply'
    assert str(result.reply) == text
    assert result.reply.draft_record['factual_claims'] == []
    assert result.reply.draft_record['model_call_count'] == 1
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize('headers, delay', [({'Retry-After': '1'}, 1), ({'Retry-After': '0.2'}, 1), ({'x-ratelimit-reset-requests': '500ms'}, 1)])
def test_short_429_recovery_still_persists_health_and_blocks_next_candidate(monkeypatch, headers, delay):
    """Success after an allowed retry does not erase the rate-limit event."""
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(429, headers=headers), FakeHttpResponse(200, body=response_envelope(raw_decision()))])
    state = bot.default_state()
    result = bot.evaluate_single_call_reply(context(), state=state)
    assert result.status == 'reply'
    assert result.provider_request_attempt_count == 2
    assert result.provider_status_code == 429
    assert sleeps == [delay]
    assert len(calls) == 2
    assert state['openai_error_epochs'] == [2_000_000_000]
    assert saves[-1]['openai_api_cooldown_until_epoch'] == 2_000_000_061
    assert bot.evaluate_single_call_reply(context(), state=state).reason == 'openai_cooldown'
    assert len(calls) == 2


@pytest.mark.parametrize('headers', [{'Retry-After': '120'}, {'Retry-After': '900000000'}, {'Retry-After': 'nonsense'}, {'x-ratelimit-reset-tokens': '2h3m'}, {}])
def test_long_or_unknown_429_defers_without_sleep_or_second_call(monkeypatch, headers):
    """Only a proved short provider delay is eligible for immediate retry."""
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(429, headers=headers)])
    state = bot.default_state()
    result = bot.evaluate_single_call_reply(context(), state=state)
    assert result.status == 'operational_failure'
    assert result.provider_request_attempt_count == 1
    assert len(calls) == 1 and sleeps == []
    assert saves[-1]['openai_api_cooldown_until_epoch'] > 2_000_000_000


def test_short_429_then_ambiguous_timeout_is_never_retried_again(monkeypatch):
    """A second ambiguous transport failure retains 429 authority and stops."""
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(429, headers={'Retry-After': '1'}), bot.requests.ReadTimeout('offline timeout')])
    result = bot.evaluate_single_call_reply(context(), state=bot.default_state())
    assert result.error_category == 'provider_ambiguous_timeout'
    assert result.provider_request_attempt_count == 2
    assert len(calls) == 2
    assert saves[-1]['openai_error_epochs'] == [2_000_000_000]


@pytest.mark.parametrize('declared, transient', [(999, True), (1, False)])
def test_root_download_checks_exact_content_length(monkeypatch, declared, transient):
    """Short image reads defer; overlong bodies are rejected as invalid input."""
    response = FakeHttpResponse(200, headers={'Content-Type': 'image/png', 'Content-Length': str(declared)})
    monkeypatch.setattr(bot.requests, 'get', lambda *_args, **_kwargs: response)
    monkeypatch.setattr(bot, 'require_remote_operation_unpaused', lambda *_: None)
    metadata = {'status': 'supplied', 'photos_expected': 1, 'photos': [{'media_key': 'm1', 'url': 'https://pbs.twimg.com/media/offline', 'attachment_role': 'target_contribution', 'source_post_id': '100'}]}
    error = bot.ReplyMediaTransientUnavailable if transient else bot.ReplyMediaUnavailable
    with pytest.raises(error, match='declared length'):
        bot.collect_reply_images(metadata)
    assert response.closed


def test_v3_draft_receipts_reconcile_but_pending_drafts_cannot_send(monkeypatch):
    """Old confirmed-post evidence remains readable without reapproving old prose."""
    from tests.helpers.reply_fixtures import unit_confirmed_v3_reply_receipt, UNIT_REPLY_REPOSITORY
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    receipt = unit_confirmed_v3_reply_receipt(text='Thank you.')
    draft = receipt['ai_reply_draft']
    draft.pop('factual_claims')
    draft.pop('time_context')
    draft['draft_schema_version'] = 3
    draft['prompt_sha256'] = bot._LEGACY_SINGLE_SOL_PROMPT_SHA256
    draft['response_schema_sha256'] = bot._LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256
    draft['validated_draft_hash'] = bot._legacy_reply_value_sha256({key: value for key, value in draft.items() if key != 'validated_draft_hash'})
    assert bot._legacy_ai_reply_receipt_draft_is_valid(receipt, receipt['reply_text'])
    with pytest.raises(ValueError):
        bot.validate_current_ai_reply_draft(draft, context=receipt['reply_context'])


def test_root_context_payload_uses_utc_calendar_date(monkeypatch):
    """Root reply assembly uses authoritative UTC despite ambient local time."""
    from datetime import datetime
    import time

    original_tz = os.environ.get('TZ')
    monkeypatch.setenv('TZ', 'Pacific/Honolulu')
    time.tzset()
    try:
        monkeypatch.setattr(bot, 'now_epoch', lambda: 1_788_480_060)  # 2026-09-04 00:01 UTC
        patch_reply_context_method(monkeypatch, 'parent_chain', lambda *_: [])
        monkeypatch.setattr(bot, 'reply_media_context_for_candidate', lambda *_args, **_kwargs: None)
        target = {'id': '100', 'author_id': '200', 'text': 'What happened today?', 'created_at': '2026-09-03T23:59:59Z', 'referenced_tweets': []}
        prepared = bot.build_context_for_reply_ai(target, bot.default_state())
        assert prepared is not None
        assert prepared.context['current_date'] == bot.current_utc_datetime().date().isoformat()
        assert prepared.context['current_date'] != datetime.fromtimestamp(bot.now_epoch()).date().isoformat()
        payload, _ = __import__('single_call_reply').build_model_payload(context=prepared.context, repository=FakeRepository())
        assert payload['time_context']['target_created_at'] == target['created_at']
    finally:
        if original_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = original_tz
        time.tzset()


def test_persisted_cooldown_blocks_provider_after_fresh_process(monkeypatch, tmp_path):
    """A new process loads saved cooldown metadata before any provider call."""
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(429, headers={'Retry-After': '120'})])
    state = bot.default_state()
    bot.evaluate_single_call_reply(context(), state=state)
    state_file = tmp_path / 'bot_state.json'
    proposed = tmp_path / 'proposed.json'
    proposed.write_text(json.dumps(saves[-1]), encoding='utf-8')
    proposed.chmod(0o600)
    code = '''
import json, os, sys
from pathlib import Path
import mrsMThatcher2 as bot
bot.STATE_FILE = Path(sys.argv[1])
bot.STATE_BACKUP_COUNT = 0
bot.now_epoch = lambda: 2_000_000_000
bot.acquire_instance_lock()
if len(sys.argv) > 2:
    bot.save_state(json.loads(Path(sys.argv[2]).read_text()), durable=True)
else:
    state = bot.load_state()
    def forbidden(*args, **kwargs):
        raise AssertionError("provider or image collection called during persisted cooldown")
    bot.requests.post = forbidden
    bot.collect_reply_images = forbidden
    result = bot.evaluate_single_call_reply({"lane": "mention", "target_id": "100"}, state=state)
    assert result.reason == "openai_cooldown", result
'''
    from tests.helpers.bot_runtime import IMPORT_ENV
    environment = {**os.environ, **IMPORT_ENV, 'MRS_BASE_DIR': str(tmp_path), 'MRS_LOG_FILE': str(tmp_path / 'child.log')}
    for args in ([str(state_file), str(proposed)], [str(state_file)]):
        result = subprocess.run([sys.executable, '-c', code, *args], cwd=Path(__file__).resolve().parents[1], env=environment, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize('kind', ['social', 'humour', 'principle', 'direct_factual', 'premise_neutral', 'clarification'])
def test_real_root_adapter_rejects_fabricated_fact_in_every_kind(monkeypatch, kind):
    """The real root path cannot turn a known unrelated fact ID into authority."""
    false = raw_decision(kind=kind, reply='Britain joined the European Economic Community in 1873.', facts=['F1'])
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(200, body=response_envelope(false))])
    state = bot.default_state()
    result = bot.evaluate_single_call_reply(context(), state=state)
    assert result.status == 'operational_failure'
    assert result.error_category == 'local_validation'
    assert result.reply is None
    assert len(calls) == 1
    assert state['openai_error_epochs'] == []


def test_real_root_adapter_accepts_independently_bound_fact(monkeypatch):
    """A supported fact becomes a publishable hash-bound root draft in one call."""
    from tests.helpers.single_call_fixtures import FakePassage
    text = 'Britain joined the European Economic Community in 1973.'
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(200, body=response_envelope(raw_decision(kind='direct_factual', reply=text, facts=['F1'])))])
    repository = FakeRepository(1)
    repository.passages['evidence-1'] = FakePassage('evidence-1', text)
    monkeypatch.setattr(bot, 'reply_evidence_repository', lambda: repository)
    result = bot.evaluate_single_call_reply(context(), state=bot.default_state())
    assert result.status == 'reply'
    assert result.reply.draft_record['factual_claims'] == [{'text': text, 'fact_ids': ['F1']}]
    assert len(calls) == 1
