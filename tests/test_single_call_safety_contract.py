"""Regressions for declared evidence, media and time after one call; no network."""
import copy
import io
import json

import pytest
from PIL import Image

import single_call_reply as pipeline
from tests.helpers.single_call_fixtures import FakePassage, FakeRepository, context, raw_decision


def payload_with_fact():
    """Supply an unrelated, valid archive fact."""
    repository = FakeRepository(1)
    repository.passages['evidence-1'] = FakePassage('evidence-1', 'Britain joined the European Economic Community in 1973.')
    payload, _ = pipeline.build_model_payload(context=context(), repository=repository)
    return payload


@pytest.mark.parametrize('kind', [kind for kind in pipeline.REPLY_KINDS if kind != 'no_reply'])
def test_false_historical_claim_rejected_in_every_kind(kind):
    """A label or known fact ID cannot authorise a false claim."""
    raw = raw_decision(kind=kind, reply='Britain joined the European Economic Community in 1873.', facts=['F1'])
    with pytest.raises(pipeline.ReplyValidationError):
        pipeline.validate_model_output(raw, payload=payload_with_fact())


@pytest.mark.parametrize('mime, data', [('image/png', b'\x89PNG\r\n\x1a\n'), ('image/jpeg', b'\xff\xd8\xff'), ('image/gif', b'GIF89a'), ('image/webp', b'RIFF\x00\x00\x00\x00WEBP')])
def test_signature_only_is_not_an_image(mime, data):
    """Magic prefixes alone are incomplete containers."""
    with pytest.raises(pipeline.ContextValidationError):
        pipeline.validate_supplied_images([image_record(mime, data)])


def image_record(mime, data):
    """Return one bounded native image binding."""
    return dict(identity='image-1', source_post_id='target', attachment_role='target_contribution', mime_type=mime, data=data)


@pytest.mark.parametrize('text', ['20°C', '© Crown copyright', '™', '№ 10'])
def test_ordinary_symbols_are_not_emoji(text):
    """Unicode other-symbol is broader than emoji."""
    assert not pipeline.contains_emoji(text)


def test_time_context_is_bound_to_payload():
    """Authoritative date and timestamp must survive payload construction."""
    source = context()
    source['target_created_at'] = '2026-09-03T10:20:30Z'
    payload, _ = pipeline.build_model_payload(context=source, repository=FakeRepository())
    assert payload['time_context'] == {'current_date': '2026-09-04', 'target_created_at': '2026-09-03T10:20:30Z'}
    changed = copy.deepcopy(source)
    changed['current_date'] = '2026-09-05'
    other, _ = pipeline.build_model_payload(context=changed, repository=FakeRepository())
    assert pipeline.value_sha256(payload) != pipeline.value_sha256(other)


@pytest.mark.parametrize('kind', [kind for kind in pipeline.REPLY_KINDS if kind not in {'no_reply', 'direct_factual'}])
def test_undeclared_factual_assertion_exposes_model_judgement_limit(kind):
    """An omitted assertion violates the prompt but is not detected semantically.

    This deliberately bad mocked inventory documents the limit, not an approved
    editorial decision or evidence that a model follows the instruction. The old
    test claimed universal local detection by rejecting all unfamiliar prose.
    """
    raw = json.loads(raw_decision(kind=kind, reply='Britain joined the European Economic Community in 1873.'))
    raw['factual_claims'] = []
    assert pipeline.validate_model_output(json.dumps(raw), payload=payload_with_fact())['factual_claims'] == []


def test_supported_fact_and_hash_binding_survive_recovery():
    """Exact whole-passage support, claims and evidence remain bound on recovery."""
    repository = FakeRepository(1)
    text = 'Britain joined the European Economic Community in 1973.'
    repository.passages['evidence-1'] = FakePassage('evidence-1', text)
    source = context()
    payload, facts = pipeline.build_model_payload(context=source, repository=repository)
    output = pipeline.validate_model_output(raw_decision(kind='direct_factual', reply=text, facts=['F1']), payload=payload)
    draft = pipeline.create_durable_draft(output=output, payload=payload, fact_map=facts, images=[], target_author_id='200')
    assert pipeline.validate_persisted_draft(draft, context=source, repository=repository) == draft
    changed = copy.deepcopy(draft)
    changed['factual_claims'][0]['text'] = text.replace('1973', '1873')
    with pytest.raises(ValueError, match='hash'):
        pipeline.validate_persisted_draft(changed, context=source, repository=repository)
    changed['validated_draft_hash'] = pipeline.value_sha256({key: value for key, value in changed.items() if key != 'validated_draft_hash'})
    with pytest.raises(pipeline.ReplyValidationError):
        pipeline.validate_persisted_draft(changed, context=source, repository=repository)
    repository.passages['evidence-1'] = FakePassage('evidence-1', text.replace('1973', '1873'))
    with pytest.raises(ValueError, match='source record'):
        pipeline.validate_persisted_draft(draft, context=source, repository=repository)


@pytest.mark.parametrize('text', ['Thank you.', 'I am sorry for your loss.', 'Responsibility matters more than rhetoric.', 'I favour individual choice.', 'What do you mean?', 'Fair enough. Take care.'])
def test_premise_neutral_conversation_remains_usable(text):
    """Conversational replies with an empty declared inventory remain usable."""
    assert pipeline.validate_model_output(raw_decision(reply=text), payload=payload_with_fact())['reply'] == text


@pytest.mark.parametrize('text', ['I think Britain joined the EEC in 1873.', 'Kindness matters because it prevents all crime.', 'Thank you, Thatcher invented ice cream.', 'Liberty matters; Britain joined the EEC in 1873.', 'Britain did not join the EEC in 1973.'])
def test_opinion_prefix_or_extra_clause_does_not_authorise_a_declared_fact(text):
    """A subjective prefix cannot prove support for an inventoried claim."""
    with pytest.raises(pipeline.ReplyValidationError):
        pipeline.validate_model_output(raw_decision(reply=text, facts=['F1']), payload=payload_with_fact())


@pytest.mark.parametrize('target', ['2026-09-03T10:20:30', '2026-09-03 10:20:30Z', '2026-09-03T10:20:30+01:00', '2026-02-30T00:00:00Z'])
def test_noncanonical_source_times_rejected(target):
    """Never infer an offset or accept an impossible source timestamp."""
    source = context()
    source['target_created_at'] = target
    with pytest.raises(pipeline.ContextValidationError):
        pipeline.build_model_payload(context=source, repository=FakeRepository())


@pytest.mark.parametrize('question', ['What happened today?', 'What happened yesterday?', 'How long ago was that?'])
def test_relative_time_questions_retain_both_time_anchors(question):
    """Temporal words travel alongside validated present and source metadata."""
    source = context(turns=1)
    source['visible_conversation'][0]['text'] = question
    source['target_created_at'] = '2026-09-03T23:59:59+00:00'
    payload, facts = pipeline.build_model_payload(context=source, repository=FakeRepository())
    assert payload['time_context']['target_created_at'] == '2026-09-03T23:59:59Z'
    output = pipeline.validate_model_output(raw_decision(), payload=payload)
    draft = pipeline.create_durable_draft(output=output, payload=payload, fact_map=facts, images=[], target_author_id='200')
    source['current_date'] = '2026-09-05'
    with pytest.raises(ValueError, match='time context'):
        pipeline.validate_persisted_draft(draft, context=source, repository=FakeRepository())


def encoded_image(format, size=(4, 4)):
    """Encode a complete deterministic local image using Pillow."""
    stream = io.BytesIO()
    Image.new('RGB', size, 'red').save(stream, format=format)
    return stream.getvalue()


@pytest.mark.parametrize('format,mime', [('JPEG', 'image/jpeg'), ('PNG', 'image/png'), ('GIF', 'image/gif'), ('WEBP', 'image/webp')])
def test_valid_and_truncated_full_image_containers(format, mime):
    """Fully valid formats decode; missing final container bytes never do."""
    data = encoded_image(format)
    assert pipeline.validate_supplied_images([image_record(mime, data)])[0]['data'] == data
    with pytest.raises(pipeline.ContextValidationError):
        pipeline.validate_supplied_images([image_record(mime, data[:-1])])


def test_decoded_dimension_and_decompression_bomb_bounds(monkeypatch):
    """Dimensions and the decoder's bomb warning are independently fatal."""
    with pytest.raises(pipeline.ContextValidationError):
        pipeline.validate_supplied_images([image_record('image/png', encoded_image('PNG', (8193, 1)))])
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 8)
    with pytest.raises(pipeline.ContextValidationError):
        pipeline.validate_supplied_images([image_record('image/png', encoded_image('PNG'))])


def test_false_mime_and_two_maximum_images_fail_before_provider():
    """The complete encoded JSON budget includes both base64 image values."""
    with pytest.raises(pipeline.ContextValidationError):
        pipeline.validate_supplied_images([image_record('image/gif', encoded_image('PNG'))])
    data = encoded_image('PNG')
    # PNG permits ancillary chunks. Insert a bounded unknown ancillary chunk
    # before IEND to make a large, valid fully decoded image without giant pixels.
    import struct
    import zlib
    payload = b'\0' * (pipeline.MAX_IMAGE_BYTES - len(data) - 12)
    chunk_type = b'tEXt'
    chunk = struct.pack('>I', len(payload)) + chunk_type + payload + struct.pack('>I', zlib.crc32(chunk_type + payload))
    large = data[:-12] + chunk + data[-12:]
    first = image_record('image/png', large)
    second = {**first, 'identity': 'image-2'}
    with pytest.raises(pipeline.ContextValidationError, match='provider request'):
        pipeline.build_openai_request(payload_with_fact(), supplied_images=[first, second])


@pytest.mark.parametrize('text', ['😀', '❤️', '🇬🇧', '1️⃣', '👩\u200d💻', '🟠', '🟩', '🟰'])
def test_actual_emoji_remain_blocked(text):
    """Pictographs, variation selectors, flags and joined emoji remain forbidden."""
    assert pipeline.contains_emoji(text)


@pytest.mark.parametrize('text', ['He was born in Blenheim Palace.', 'Today Britain joined the European Economic Community.', 'That happened in 1973.', 'Britain joined yesterday.', 'I was born in 1925.'])
def test_exact_text_with_unbound_referent_is_not_portable_factual_authority(text):
    """Copying bytes cannot carry a passage's pronoun or relative-time referent."""
    payload = payload_with_fact()
    payload['trusted_facts'] = [{'id': 'F1', 'passage': text}]
    with pytest.raises(pipeline.ReplyValidationError, match='unsupported_factual_claim'):
        pipeline.validate_model_output(raw_decision(kind='direct_factual', reply=text, facts=['F1']), payload=payload)


def test_complete_wire_request_limit_counts_json_unicode_escaping(monkeypatch):
    """Enforce the actual json= encoding at the exact accepted byte boundary."""
    payload = {'prose': 'é' * 100}
    image = image_record('image/png', encoded_image('PNG'))
    request, _ = pipeline.build_openai_request(payload, supplied_images=[image])
    wire_bytes = json.dumps(request, allow_nan=False).encode('utf-8')
    assert len(wire_bytes) > len(pipeline.canonical_bytes(request))
    monkeypatch.setattr(pipeline, 'MAX_ENCODED_PROVIDER_REQUEST_BYTES', len(wire_bytes))
    assert pipeline.build_openai_request(payload, supplied_images=[image])[0] == request
    monkeypatch.setattr(pipeline, 'MAX_ENCODED_PROVIDER_REQUEST_BYTES', len(wire_bytes) - 1)
    with pytest.raises(pipeline.ContextValidationError, match='provider request'):
        pipeline.build_openai_request(payload, supplied_images=[image])
