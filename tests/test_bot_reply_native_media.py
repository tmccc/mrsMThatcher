from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_native_media as native_media
import mrs_bot_reply_generation as generation
from tests.helpers.reply_fixtures import image_case
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


OWNER_INPUTS = {
    "maximum_context_photos": "MAX_REPLY_CONTEXT_PHOTOS",
    "maximum_supplied_images": "MAX_SUPPLIED_IMAGES",
    "maximum_image_bytes": "SINGLE_CALL_MAX_IMAGE_BYTES",
    "image_mime_types": "_REPLY_IMAGE_MIME_TYPES",
    "log": "log",
    "media_unavailable": "ReplyMediaUnavailable",
    "media_transient_unavailable": "ReplyMediaTransientUnavailable",
    "test_mode": "TEST_MODE",
    "require_remote_operation_unpaused": "require_remote_operation_unpaused",
    "requests": "requests", "request_timeout": "request_timeout",
    "validate_supplied_images": "validate_supplied_images",
}


@pytest.fixture
def make_owner():
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        return native_media.ReplyMedia(**{**current, **overrides})
    return build


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('native reply media import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_native_media', 'mrs_bot_request_route_values'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_native_media
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_owner_composition_captures_current_dependencies_without_calling_them(monkeypatch):
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._reply_media_owner()
        assert isinstance(owner, native_media.ReplyMedia)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, field) is value for field, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.maximum_context_photos = 1


def test_aliases_and_adapters_keep_defaults_references_and_errors(monkeypatch):
    assert bot.attach_media_to_tweets is native_media.attach_media_to_tweets
    assert bot._REPLY_IMAGE_MIME_TYPES is generation._REPLY_IMAGE_MIME_TYPES is native_media._REPLY_IMAGE_MIME_TYPES
    assert type(native_media._REPLY_IMAGE_MIME_TYPES) is set
    assert native_media._REPLY_IMAGE_MIME_TYPES == {"image/jpeg", "image/png", "image/webp", "image/gif"}
    for name, method_name in (
        ("candidate_native_photo_media", "candidate_photos"),
        ("reply_media_context_for_candidate", "context"),
        ("collect_reply_images", "collect"),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter)
        owned = inspect.signature(getattr(native_media.ReplyMedia, method_name))
        assert [(p.name, p.kind, p.default) for p in public.parameters.values()] == [
            (p.name, p.kind, p.default) for p in list(owned.parameters.values())[1:]
        ]
        args = tuple(object() for p in public.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, p in public.parameters.items() if p.kind == p.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for include_defaults in (False, True):
                owner = Mock(spec=native_media.ReplyMedia)
                factory = Mock(return_value=owner)
                patch.setattr(bot, "_reply_media_owner", factory)
                implementation = getattr(owner, method_name)
                result = object()
                implementation.return_value = result
                supplied = {key: value for key, value in options.items()
                            if include_defaults or public.parameters[key].default is inspect.Parameter.empty}
                bound = public.bind(*args, **supplied)
                bound.apply_defaults()
                expected = {key: value for key, value in bound.arguments.items()
                            if public.parameters[key].kind == inspect.Parameter.KEYWORD_ONLY}
                assert adapter(*args, **supplied) is result
                factory.assert_called_once_with()
                actual_args, actual_kwargs = implementation.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError("current owner failure")
            implementation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **supplied)
            assert caught.value is failure


def test_attachment_keeps_expansion_identity_duplicates_no_clear_and_fresh_photo_records(make_owner, image_case):
    _, media = image_case
    photo = {**media["photos"][0], "type": "PHOTO", "media_key": 7}
    photo["url"] = f"  {photo['url']}  "
    earlier = {**photo, "url": "ignored earlier expansion"}
    includes = {"media": [earlier, photo]}
    original_includes = copy.deepcopy(includes)
    retained = [{"previous": "attachment"}]
    target = {"attachments": {"media_keys": [7, "7", "missing"]}}
    unmatched = {"attachments": {"media_keys": ["missing"]}, "_attached_media": retained}
    malformed = {"attachments": {"media_keys": "7"}, "_attached_media": retained}
    assert native_media.attach_media_to_tweets([target, unmatched, malformed], includes) is None
    attached = target["_attached_media"]
    assert len(attached) == 2 and all(row is photo for row in attached)
    assert unmatched["_attached_media"] is malformed["_attached_media"] is retained
    native_media.attach_media_to_tweets([target], {"media": []})
    native_media.attach_media_to_tweets([target], {"media": "malformed"})
    assert target["_attached_media"] is attached

    owner = make_owner(maximum_context_photos=1)
    selected, expected = owner.candidate_photos(target)
    assert selected == [{"media_key": "7", "url": photo["url"].strip()}]
    assert expected == 3
    selected[0]["url"] = "changed result"
    owner = replace(owner, maximum_context_photos=2)
    selected, expected = owner.candidate_photos(target)
    assert expected == 3 and len(selected) == 2
    assert selected[0] == selected[1] and selected[0] is not selected[1]
    assert all(row is not photo for row in selected)
    assert includes == original_includes
    with pytest.raises(AttributeError):
        native_media.attach_media_to_tweets([None], includes)


def test_context_keeps_target_duplicates_quote_dedup_current_order_logger_and_error(monkeypatch, make_owner, image_case):
    _, media = image_case
    photo = {**media["photos"][0], "type": "photo"}
    quoted_photo = {**photo, "media_key": "quoted-photo"}
    target = {"id": 100, "_attached_media": [photo, photo]}
    quoted = {"id": 90, "_attached_media": [photo, quoted_photo, quoted_photo]}
    before = copy.deepcopy((target, quoted))
    callback = native_media.ReplyMedia.candidate_photos
    current = Mock(side_effect=lambda candidate: callback(owner, candidate))
    logger = Mock()
    owner = make_owner(maximum_context_photos=3, log=logger)
    monkeypatch.setattr(native_media.ReplyMedia, "candidate_photos", lambda self, candidate: current(candidate))
    result = owner.context(target, lane="mention", target_id=100, quoted_candidate=quoted)
    assert current.call_args_list == [call(target), call(quoted)]
    assert current.call_args_list[0].args[0] is target
    assert current.call_args_list[1].args[0] is quoted
    target_record = {**media["photos"][0], "source_post_id": "100"}
    quote_record = {**target_record, "media_key": "quoted-photo", "attachment_role": "quoted_subject", "source_post_id": "90"}
    assert result == {
        "lane": "mention", "target_id": "100", "mode": "multimodal",
        "status": "supplied", "photos_expected": 3,
        "photos": [target_record, target_record, quote_record],
    }
    assert result["photos"][0] is not result["photos"][1]
    assert all(row is not photo and row is not quoted_photo for row in result["photos"])
    logger.info.assert_called_once_with(
        "Reply media context lane=%s target_id=%s photos=%d mode=multimodal status=supplied",
        "mention", 100, 3,
    )
    logger.warning.assert_not_called()
    assert (target, quoted) == before

    current, logger = Mock(side_effect=lambda candidate: callback(owner, candidate)), Mock()
    owner = replace(owner, maximum_context_photos=2, log=logger)
    result = owner.context(target, lane="mention", target_id=100, quoted_candidate=quoted)
    current.assert_called_once_with(target)
    assert result["photos_expected"] == len(result["photos"]) == 2
    logger.info.assert_called_once()
    logger.reset_mock()
    current.reset_mock()
    failure = TypeError("candidate callback failure")
    current.side_effect = failure
    with pytest.raises(TypeError) as caught:
        owner.context(target, lane="mention", target_id=100, quoted_candidate=quoted)
    assert caught.value is failure
    current.assert_called_once_with(target)
    assert logger.mock_calls == []


def test_unresolved_and_unclassified_metadata_clear_selection_and_preserve_expected_counts(make_owner, image_case):
    _, media = image_case
    photo = {**media["photos"][0], "type": "photo"}
    target = {
        "attachments": {"media_keys": [photo["media_key"], "missing", "unknown", "video", "gif"]},
        "_attached_media": [photo, {"media_key": "unknown", "type": " photo"},
                            {"media_key": "video", "type": "video"},
                            {"media_key": "gif", "type": "animated_gif"}],
    }
    quoted = {"id": "90", "_attached_media": [{**photo, "media_key": "quote"}]}
    before = copy.deepcopy((target, quoted))
    logger = Mock()
    owner = make_owner(maximum_context_photos=2, log=logger)
    selected, expected = owner.candidate_photos(target)
    assert len(selected) == 1 and expected == 3
    assert owner.context(target, lane="mention", target_id=100, quoted_candidate=quoted) == {
        "lane": "mention", "target_id": "100", "mode": "multimodal",
        "status": "unavailable", "photos_expected": 4, "photos": [],
    }
    logger.warning.assert_called_once_with(
        "Reply media context unavailable lane=%s target_id=%s photos_expected=%d mode=multimodal status=unavailable",
        "mention", 100, 4,
    )
    logger.info.assert_not_called()
    assert (target, quoted) == before
    assert owner.candidate_photos({"attachments": {"media_keys": "missing"}, "_attached_media": "malformed"}) == ([], 1)
    assert owner.candidate_photos({"attachments": None}) == ([], 0)
    logger.reset_mock()
    assert owner.context({}, lane="mention", target_id=100) == {
        "lane": "mention", "target_id": "100", "mode": "none",
        "status": "none", "photos_expected": 0, "photos": [],
    }
    assert logger.mock_calls == []


def test_image_collection_uses_current_requests_bounds_and_validation_reference(make_owner, image_case):
    response, media = image_case
    chunks = list(response.iter_content(chunk_size=64 * 1024))
    data = b"".join(chunks)
    response.iter_content = Mock(return_value=iter([None, b"", bytearray(b"ignored"), *chunks]))
    response.headers["Content-Length"] = str(len(data))
    trace = Mock()
    trace.get.return_value = response
    trace.timeout.return_value = 17
    response.close = trace.close = Mock(wraps=response.close)

    def validate(images):
        assert response.closed
        return images

    trace.validate.side_effect = validate
    owner = make_owner(
        requests=SimpleNamespace(get=trace.get, RequestException=bot.requests.RequestException),
        request_timeout=trace.timeout, require_remote_operation_unpaused=trace.pause,
        validate_supplied_images=trace.validate, maximum_supplied_images=1,
        maximum_image_bytes=len(data), image_mime_types={"image/png"},
    )

    result = owner.collect(media)
    assert result is trace.validate.call_args.args[0]
    assert result == [{
        "identity": "native-photo", "mime_type": "image/png", "data": data,
        "attachment_role": "target_contribution", "source_post_id": "target",
    }]
    assert [entry[0] for entry in trace.mock_calls] == ["pause", "timeout", "get", "close", "validate"]
    trace.pause.assert_called_once_with("candidate image collection 1/1")
    trace.get.assert_called_once_with(
        media["photos"][0]["url"], stream=True, allow_redirects=False, timeout=17,
        headers={"Accept": "image/jpeg,image/png,image/webp,image/gif", "Accept-Encoding": "identity"},
    )
    response.iter_content.assert_called_once_with(chunk_size=64 * 1024)


@pytest.mark.parametrize("failure_site", ["stream", "validation"])
def test_image_failure_closes_response_before_propagating_original_cause(make_owner, image_case, failure_site):
    response, media = image_case
    failure = bot.requests.Timeout("fixture stream failure") if failure_site == "stream" else ValueError("fixture validation failure")
    validate = Mock(side_effect=failure)
    if failure_site == "stream":
        response.iter_content = Mock(side_effect=failure)
    owner = make_owner(
        requests=SimpleNamespace(get=Mock(return_value=response), RequestException=bot.requests.RequestException),
        require_remote_operation_unpaused=Mock(), validate_supplied_images=validate,
    )
    response.close = Mock(wraps=response.close)
    expected = bot.ReplyMediaTransientUnavailable if failure_site == "stream" else bot.ReplyMediaUnavailable
    with pytest.raises(expected) as caught:
        owner.collect(media)
    assert type(caught.value) is expected
    assert caught.value.__cause__ is failure
    assert response.closed
    response.close.assert_called_once_with()
    assert validate.call_count == int(failure_site == "validation")


@pytest.mark.parametrize("second_stream_fails", [False, True])
def test_multiple_image_collection_closes_each_download_before_the_next_step(
    make_owner, image_case, second_stream_fails,
):
    first, media = image_case
    second = copy.deepcopy(first)
    media["photos"].append({
        **media["photos"][0], "media_key": "quoted-photo",
        "url": "http://127.0.0.1/media/quoted.png",
        "attachment_role": "quoted_subject", "source_post_id": "quoted",
    })
    media["photos_expected"] = 2
    failure = bot.requests.Timeout("fixture second image stream failure")
    if second_stream_fails:
        second.iter_content = Mock(side_effect=failure)
    trace = Mock()
    trace.get.side_effect = [first, second]
    first.close = trace.first_close = Mock(wraps=first.close)
    second.close = trace.second_close = Mock(wraps=second.close)
    trace.validate.side_effect = lambda images: images
    owner = make_owner(
        requests=SimpleNamespace(get=trace.get, RequestException=bot.requests.RequestException),
        require_remote_operation_unpaused=trace.pause,
        validate_supplied_images=trace.validate, maximum_supplied_images=2,
        request_timeout=Mock(return_value=17),
    )

    if second_stream_fails:
        with pytest.raises(bot.ReplyMediaTransientUnavailable) as caught:
            owner.collect(media)
        assert caught.value.__cause__ is failure
        trace.validate.assert_not_called()
    else:
        result = owner.collect(media)
        assert result is trace.validate.call_args.args[0]
        assert [(image["identity"], image["attachment_role"], image["source_post_id"])
                for image in result] == [
            ("native-photo", "target_contribution", "target"),
            ("quoted-photo", "quoted_subject", "quoted"),
        ]
    expected_order = ["pause", "get", "first_close", "pause", "get", "second_close"]
    assert [entry[0] for entry in trace.mock_calls] == (
        expected_order if second_stream_fails else [*expected_order, "validate"]
    )
    assert trace.pause.call_args_list == [
        call("candidate image collection 1/2"), call("candidate image collection 2/2"),
    ]
    assert first.closed and second.closed


def test_url_validation_reads_current_policy_and_preserves_exception_cause(monkeypatch, make_owner):
    class CurrentMediaError(bot.ReplyMediaUnavailable):
        pass

    owner = make_owner(media_unavailable=CurrentMediaError, test_mode=False)
    assert owner.safe_url(" https://pbs.twimg.com:443/media/photo.png ") == "https://pbs.twimg.com:443/media/photo.png"
    with pytest.raises(CurrentMediaError, match="trusted X media origin"):
        owner.safe_url("http://127.0.0.1/media/photo.png")
    owner = replace(owner, test_mode=True)
    assert owner.safe_url("http://127.0.0.1/media/photo.png") == "http://127.0.0.1/media/photo.png"
    failure = ValueError("fixture invalid port")
    monkeypatch.setattr(native_media, "urlsplit", Mock(side_effect=failure))
    with pytest.raises(CurrentMediaError, match="invalid port") as caught:
        owner.safe_url("https://pbs.twimg.com:bad/media/photo.png")
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize("status, headers, expected_error, message", [
    (503, {"Content-Encoding": "gzip"}, "ReplyMediaTransientUnavailable", "HTTP 503"),
    (200, {"Content-Encoding": "gzip", "Location": "/other"}, "ReplyMediaUnavailable", "transfer encoding"),
    (200, {"Location": "/other", "Content-Type": "text/html"}, "ReplyMediaUnavailable", "redirect"),
    (200, {"Content-Type": "text/html", "Content-Length": "invalid"}, "ReplyMediaUnavailable", "type is unsupported"),
    (200, {"Content-Length": "invalid"}, "ReplyMediaUnavailable", "length is invalid"),
    (200, {"Content-Length": "0"}, "ReplyMediaUnavailable", "length is outside"),
])
def test_response_rejection_keeps_validation_order_and_closes_before_streaming(
    make_owner, image_case, status, headers, expected_error, message,
):
    response, media = image_case
    response.status_code = status
    response.headers.update(headers)
    response.iter_content = Mock(side_effect=AssertionError("unsafe response was streamed"))
    response.close = Mock(wraps=response.close)
    validation = Mock(side_effect=AssertionError("unsafe bytes reached image validation"))
    owner = make_owner(
        requests=SimpleNamespace(get=Mock(return_value=response), RequestException=bot.requests.RequestException),
        require_remote_operation_unpaused=Mock(), validate_supplied_images=validation,
    )

    with pytest.raises(getattr(bot, expected_error), match=message) as caught:
        owner.collect(media)
    assert type(caught.value) is getattr(bot, expected_error)
    assert response.closed
    response.close.assert_called_once_with()
    response.iter_content.assert_not_called()
    validation.assert_not_called()
