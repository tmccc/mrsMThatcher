from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_native_media as native_media
from tests.test_bot_reply_generation import image_case
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('native reply media import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_native_media':
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


def test_alias_and_adapters_keep_current_dependencies_defaults_references_and_errors(monkeypatch):
    assert bot.attach_media_to_tweets is native_media.attach_media_to_tweets
    candidate, quoted, result = {}, {}, object()
    for name, dependencies in (
        ("candidate_native_photo_media", ("MAX_REPLY_CONTEXT_PHOTOS",)),
        ("reply_media_context_for_candidate", (
            "MAX_REPLY_CONTEXT_PHOTOS", "candidate_native_photo_media", "log",
        )),
    ):
        adapter = getattr(bot, name)
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(native_media, name, owner)
            for explicit_quote in (False, True):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                options, expected = {}, dict(current)
                if name == "reply_media_context_for_candidate":
                    options = {"lane": object(), "target_id": object()}
                    if explicit_quote:
                        options["quoted_candidate"] = quoted
                    expected.update({"quoted_candidate": None, **options})
                assert adapter(candidate, **options) is result
                args, kwargs = owner.call_args
                assert len(args) == 1 and args[0] is candidate
                assert kwargs.keys() == expected.keys()
                assert all(kwargs[key] is value for key, value in expected.items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(candidate, **options)
            assert caught.value is failure


def test_attachment_keeps_expansion_identity_duplicates_no_clear_and_fresh_photo_records(monkeypatch, image_case):
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
    assert bot.attach_media_to_tweets([target, unmatched, malformed], includes) is None
    attached = target["_attached_media"]
    assert len(attached) == 2 and all(row is photo for row in attached)
    assert unmatched["_attached_media"] is malformed["_attached_media"] is retained
    bot.attach_media_to_tweets([target], {"media": []})
    bot.attach_media_to_tweets([target], {"media": "malformed"})
    assert target["_attached_media"] is attached

    monkeypatch.setattr(bot, "MAX_REPLY_CONTEXT_PHOTOS", 1)
    selected, expected = bot.candidate_native_photo_media(target)
    assert selected == [{"media_key": "7", "url": photo["url"].strip()}]
    assert expected == 3
    selected[0]["url"] = "changed result"
    monkeypatch.setattr(bot, "MAX_REPLY_CONTEXT_PHOTOS", 2)
    selected, expected = bot.candidate_native_photo_media(target)
    assert expected == 3 and len(selected) == 2
    assert selected[0] == selected[1] and selected[0] is not selected[1]
    assert all(row is not photo for row in selected)
    assert includes == original_includes
    with pytest.raises(AttributeError):
        bot.attach_media_to_tweets([None], includes)


def test_context_keeps_target_duplicates_quote_dedup_current_order_logger_and_error(monkeypatch, image_case):
    _, media = image_case
    photo = {**media["photos"][0], "type": "photo"}
    quoted_photo = {**photo, "media_key": "quoted-photo"}
    target = {"id": 100, "_attached_media": [photo, photo]}
    quoted = {"id": 90, "_attached_media": [photo, quoted_photo, quoted_photo]}
    before = copy.deepcopy((target, quoted))
    callback = bot.candidate_native_photo_media
    current = Mock(wraps=callback)
    logger = Mock()
    monkeypatch.setattr(bot, "candidate_native_photo_media", current)
    monkeypatch.setattr(bot, "MAX_REPLY_CONTEXT_PHOTOS", 3)
    monkeypatch.setattr(bot, "log", logger)
    result = bot.reply_media_context_for_candidate(target, lane="mention", target_id=100, quoted_candidate=quoted)
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

    current, logger = Mock(wraps=callback), Mock()
    monkeypatch.setattr(bot, "candidate_native_photo_media", current)
    monkeypatch.setattr(bot, "MAX_REPLY_CONTEXT_PHOTOS", 2)
    monkeypatch.setattr(bot, "log", logger)
    result = bot.reply_media_context_for_candidate(target, lane="mention", target_id=100, quoted_candidate=quoted)
    current.assert_called_once_with(target)
    assert result["photos_expected"] == len(result["photos"]) == 2
    logger.info.assert_called_once()
    logger.reset_mock()
    current.reset_mock()
    failure = TypeError("candidate callback failure")
    current.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.reply_media_context_for_candidate(target, lane="mention", target_id=100, quoted_candidate=quoted)
    assert caught.value is failure
    current.assert_called_once_with(target)
    assert logger.mock_calls == []


def test_unresolved_and_unclassified_metadata_clear_selection_and_preserve_expected_counts(monkeypatch, image_case):
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
    monkeypatch.setattr(bot, "MAX_REPLY_CONTEXT_PHOTOS", 2)
    monkeypatch.setattr(bot, "log", logger)
    selected, expected = bot.candidate_native_photo_media(target)
    assert len(selected) == 1 and expected == 3
    assert bot.reply_media_context_for_candidate(target, lane="mention", target_id=100, quoted_candidate=quoted) == {
        "lane": "mention", "target_id": "100", "mode": "multimodal",
        "status": "unavailable", "photos_expected": 4, "photos": [],
    }
    logger.warning.assert_called_once_with(
        "Reply media context unavailable lane=%s target_id=%s photos_expected=%d mode=multimodal status=unavailable",
        "mention", 100, 4,
    )
    logger.info.assert_not_called()
    assert (target, quoted) == before
    assert bot.candidate_native_photo_media({"attachments": {"media_keys": "missing"}, "_attached_media": "malformed"}) == ([], 1)
    assert bot.candidate_native_photo_media({"attachments": None}) == ([], 0)
    logger.reset_mock()
    assert bot.reply_media_context_for_candidate({}, lane="mention", target_id=100) == {
        "lane": "mention", "target_id": "100", "mode": "none",
        "status": "none", "photos_expected": 0, "photos": [],
    }
    assert logger.mock_calls == []
