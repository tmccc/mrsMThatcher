"""Independent adversarial checks of factual referents and provider health ordering."""
from __future__ import annotations

import pytest

import single_call_reply as pipeline
import mrs_bot_reply_generation as generation
from tests.helpers.single_call_fixtures import FakeRepository, FakePassage, context, raw_decision, FakeHttpResponse, response_envelope
from tests.helpers.bot_runtime import bot
from tests.test_single_call_transport_safety import configure


@pytest.mark.parametrize('text', ['He was born in Blenheim Palace.', 'Today Britain joined the European Economic Community.'])
def test_copied_evidence_cannot_change_pronoun_or_time_referents(text):
    repository = FakeRepository(1)
    repository.passages['evidence-1'] = FakePassage('evidence-1', text)
    source = context(turns=1)
    source['visible_conversation'][0]['text'] = 'Where was Isaac Newton born?'
    source['incoming_contribution'] = source['visible_conversation'][0]['text']
    payload, _ = pipeline.build_model_payload(context=source, repository=repository)
    with pytest.raises(pipeline.ReplyValidationError):
        pipeline.validate_model_output(raw_decision(kind='direct_factual', reply=text, facts=['F1']), payload=payload)


def test_recovered_429_is_durable_before_decision_telemetry(monkeypatch):
    calls, sleeps, saves = configure(monkeypatch, [FakeHttpResponse(429, headers={'Retry-After':'1'}), FakeHttpResponse(200, body=response_envelope(raw_decision()))])
    monkeypatch.setattr(generation.ReplyGeneration, 'record_result', lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('telemetry unavailable')))
    state = bot.default_state()
    with pytest.raises(OSError, match="telemetry unavailable"):
        bot._reply_assembly()._reply_generation_owner().evaluate(context(), state=state)
    assert state['openai_api_cooldown_until_epoch'] > bot.now_epoch()
    assert saves[-1]['openai_api_cooldown_until_epoch'] > bot.now_epoch()
    assert len(calls) == 2
