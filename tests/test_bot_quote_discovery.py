from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_quote_discovery as discovery
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('quote discovery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_quote_discovery':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_quote_discovery
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    counts = {
        "quote_repeated_cursor_suppression_record": 4,
        "normalise_quote_repeated_cursor_suppressions": 3,
        "load_extra_quote_watch_post_ids": 3,
        "build_quote_lookup_post_ids": 5,
        "get_recent_own_post_ids_for_quote_lookup": 2,
        "get_quote_tweets_for_post": 15,
        "get_quote_tweets_for_posts": 9,
    }
    for name, count in counts.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(discovery, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for p in public.values() if p.kind == p.POSITIONAL_OR_KEYWORD)
        kwargs = {p.name: object() for p in public.values() if p.kind == p.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(discovery, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **kwargs) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is expected for actual, expected in zip(actual_args, args))
                expected_kwargs = {**kwargs, **current}
                assert actual_kwargs.keys() == expected_kwargs.keys()
                assert all(actual_kwargs[key] is value for key, value in expected_kwargs.items())
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **kwargs)
            assert caught.value is failure

    owner = Mock()
    monkeypatch.setattr(discovery, "get_quote_tweets_for_post", owner)
    bot.get_quote_tweets_for_post("900")
    assert owner.call_args.args == ("900", None)
    monkeypatch.setattr(discovery, "normalise_quote_repeated_cursor_suppressions", owner)
    bot.normalise_quote_repeated_cursor_suppressions({})
    assert owner.call_args.kwargs["current_epoch"] is None
    monkeypatch.setattr(discovery, "quote_repeated_cursor_suppression_record", owner)
    bot.quote_repeated_cursor_suppression_record("900", {}, current_epoch=100)
    assert owner.call_args.kwargs["allow_expired"] is False


def test_watch_file_rereads_utf8_parsing_deduplication_and_post_append_cap(tmp_path, monkeypatch):
    path = tmp_path / "watch.txt"
    log = Mock()
    monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", path)
    monkeypatch.setattr(bot, "MAX_EXTRA_QUOTE_WATCH_POSTS", 10)
    monkeypatch.setattr(bot, "log", log)
    assert bot.load_extra_quote_watch_post_ids() == []
    path.write_text("  # comment\n\n 900 # inline\n900\n901\t# invalid\n902#invalid\n²\n００３\n", encoding="utf-8")
    assert bot.load_extra_quote_watch_post_ids() == ["900", "²", "００３"]
    assert log.warning.call_args_list == [
        call("Ignoring invalid extra quote-watch post ID in %s: %r", path, "901\t# invalid"),
        call("Ignoring invalid extra quote-watch post ID in %s: %r", path, "902#invalid"),
    ]
    path.write_text("904\n905\n906\n", encoding="utf-8")
    monkeypatch.setattr(bot, "MAX_EXTRA_QUOTE_WATCH_POSTS", 2)
    assert bot.load_extra_quote_watch_post_ids() == ["904", "905"]
    monkeypatch.setattr(bot, "MAX_EXTRA_QUOTE_WATCH_POSTS", 0)
    assert bot.load_extra_quote_watch_post_ids() == ["904"]


def test_watch_file_exists_error_propagates_and_read_failure_discards_partial_ids(monkeypatch):
    path, log = Mock(), Mock()
    failure = OSError("unreadable watch file")
    monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", path)
    monkeypatch.setattr(bot, "MAX_EXTRA_QUOTE_WATCH_POSTS", 10)
    monkeypatch.setattr(bot, "log", log)
    path.exists.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.load_extra_quote_watch_post_ids()
    assert caught.value is failure
    path.read_text.assert_not_called()
    log.warning.assert_not_called()

    def broken_lines():
        yield "900"
        raise failure

    path.exists.side_effect = None
    path.exists.return_value = True
    path.read_text.return_value.splitlines.return_value = broken_lines()
    assert bot.load_extra_quote_watch_post_ids() == []
    path.read_text.assert_called_once_with(encoding="utf-8")
    log.warning.assert_called_once_with(
        "Could not read extra quote-watch post IDs from %s: %s", path, failure,
    )


def test_lookup_seeds_before_watch_read_and_preserves_priority_references_and_logs(monkeypatch):
    state = {}
    recent = ["900", " 901 ", "900", "", None]
    extras = [" 902 ", "900", "902"]
    events, log = [], Mock()
    path = object()

    def seed(current):
        assert current is state
        events.append("seed")
        current["recent_own_post_ids"] = recent
        current["last_main_post_id"] = 903

    def watch():
        assert events[-1] == "seed"
        assert state["recent_own_post_ids"] is recent
        events.append("watch")
        return extras

    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", seed)
    monkeypatch.setattr(bot, "load_extra_quote_watch_post_ids", watch)
    monkeypatch.setattr(bot, "QUOTE_POST_LOOKBACK_MAIN_POSTS", 5)
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 4)
    monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", path)
    monkeypatch.setattr(bot, "log", log)
    assert bot.build_quote_lookup_post_ids(state) == ["902", "900", "903", "901"]
    assert events == ["seed", "watch"]
    assert recent == ["900", " 901 ", "900", "", None]
    assert extras == [" 902 ", "900", "902"]
    assert state["recent_own_post_ids"] is recent
    assert log.info.call_args_list == [
        call("Quote-tweet check loaded %d extra watched post(s) from %s: %s", 3, path, " 902 , 900, 902"),
        call("Own posts for quote lookup: %s", ["902", "900", "903", "901"]),
    ]
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 0)
    assert bot.build_quote_lookup_post_ids(state) == ["902"]
    assert bot.get_recent_own_post_ids_for_quote_lookup(state) == ["903", "900", " 901 ", "", "None"]
    monkeypatch.setattr(bot, "QUOTE_POST_LOOKBACK_MAIN_POSTS", -1)
    assert bot.get_recent_own_post_ids_for_quote_lookup(state) == ["903", "900", " 901 ", ""]


def test_suppression_expiration_max_epoch_exact_types_and_canonical_copy(monkeypatch):
    monkeypatch.setattr(bot, "QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS", 10)
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", 100)
    record = {"cursor_sha256": "a" * 64, "detected_epoch": 90, "retry_after_epoch": 100}
    actual = bot.quote_repeated_cursor_suppression_record("9" * 30, record, current_epoch=99)
    assert actual == record and actual is not record
    assert bot.quote_repeated_cursor_suppression_record("900", record, current_epoch=100) is None
    assert bot.quote_repeated_cursor_suppression_record("900", record, current_epoch=100, allow_expired=True) == record
    assert bot.quote_repeated_cursor_suppression_record("900", record, current_epoch=89) is None
    assert bot.quote_repeated_cursor_suppression_record("9" * 31, record, current_epoch=99) is None
    assert bot.quote_repeated_cursor_suppression_record(900, record, current_epoch=99) is None
    for changes in (
        {"cursor_sha256": "A" * 64}, {"detected_epoch": True},
        {"retry_after_epoch": 100.0}, {"extra": 1},
        {"detected_epoch": 91, "retry_after_epoch": 101},
        {"retry_after_epoch": 99},
    ):
        assert bot.quote_repeated_cursor_suppression_record("900", {**record, **changes}, current_epoch=99) is None


def test_normalization_clock_tie_sort_cap_copies_and_discarded_change_accounting(monkeypatch):
    clock = Mock(return_value=105)
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS", 10)
    monkeypatch.setattr(bot, "QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES", 2)
    record = {"cursor_sha256": "a" * 64, "detected_epoch": 100, "retry_after_epoch": 110}
    source = {"10": record, "9": record, "2": {**record, "detected_epoch": 101, "retry_after_epoch": 111}}
    before = copy.deepcopy(source)
    result, discarded = bot.normalise_quote_repeated_cursor_suppressions(source)
    assert list(result) == ["2", "9"]
    assert discarded == 1
    assert source == before
    assert all(result[key] is not source[key] for key in result)
    clock.assert_called_once_with()
    clock.reset_mock()
    assert bot.normalise_quote_repeated_cursor_suppressions(source, current_epoch=111) == ({}, 3)
    assert bot.normalise_quote_repeated_cursor_suppressions([], current_epoch=105) == ({}, 1)
    clock.assert_not_called()

    canonical = {**record, "cursor_sha256": "b" * 64}
    validator = Mock(return_value=canonical)
    monkeypatch.setattr(bot, "quote_repeated_cursor_suppression_record", validator)
    result, discarded = bot.normalise_quote_repeated_cursor_suppressions({"900": record}, current_epoch=105)
    validator.assert_called_once_with("900", record, current_epoch=105)
    assert result["900"] is canonical
    assert discarded == 1


def test_discovery_passes_paginator_contract_and_preserves_media_author_data_identity(monkeypatch):
    data = [{"id": "1", "author_id": "7"}, {"id": "2"}, {"id": "3"}]
    user = {"id": 7, "name": "author"}
    includes = {"users": [user], "media": []}
    old_tokens = {900: 123, 901: 0, 902: ""}
    state = {"quote_lookup_pagination_tokens": old_tokens}
    events, saves, log = [], Mock(), Mock()
    request = Mock(side_effect=AssertionError("stub paginator must not request"))

    def paginate(callback, path, params, **kwargs):
        assert callback is request
        assert path == "/2/tweets/900/quote_tweets"
        assert params == {
            "max_results": 17,
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities,note_tweet",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "description,username,name,public_metrics",
            "media.fields": "media_key,type,url,preview_image_url",
            "pagination_token": "123",
        }
        assert set(kwargs) == {"max_pages", "label", "on_invalid_cursor", "on_repeated_cursor", "on_page", "should_request_cursor"}
        assert kwargs["max_pages"] == 4
        assert kwargs["label"] == "quote tweets for 900"
        assert all(callable(kwargs[key]) for key in ("on_invalid_cursor", "on_repeated_cursor", "on_page", "should_request_cursor"))
        assert kwargs["should_request_cursor"]("unseen") is True
        events.append("paginate")
        return {"data": data, "includes": includes, "_pagination": {"next_token": "tail"}}

    def media(tweets, expanded):
        assert tweets is data and expanded is includes
        assert all("_author_user" not in tweet for tweet in tweets)
        events.append("media")

    def debug(label, tweets):
        assert label == "Quote tweets returned" and tweets is data
        assert tweets[0]["_author_user"] is user
        events.append("debug")

    monkeypatch.setattr(bot, "QUOTE_LOOKUP_API_MAX_RESULTS", 17)
    monkeypatch.setattr(bot, "QUOTE_LOOKUP_MAX_PAGES_PER_POST", 4)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "x_paginated_get", paginate)
    monkeypatch.setattr(bot, "attach_media_to_tweets", media)
    monkeypatch.setattr(bot, "log_json_debug", debug)
    monkeypatch.setattr(bot, "save_state", saves)
    monkeypatch.setattr(bot, "log", log)
    assert bot.get_quote_tweets_for_post(900, state) is data
    assert events == ["paginate", "media", "debug"]
    assert data[1]["_author_user"] == data[2]["_author_user"] == {}
    assert data[1]["_author_user"] is not data[2]["_author_user"]
    assert state["quote_lookup_pagination_tokens"] == {"900": "tail", "901": "0"}
    assert state["quote_lookup_pagination_tokens"] is not old_tokens
    assert old_tokens == {900: 123, 901: 0, 902: ""}
    log.info.assert_called_with("Fetched %d quote tweet(s) for post_id=%s", 3, "900")
    saves.assert_not_called()


def test_discovery_state_none_and_native_result_errors_keep_callback_order(monkeypatch):
    saves, attach = Mock(), Mock()
    paginate = Mock(return_value={"data": [], "includes": None})
    monkeypatch.setattr(bot, "x_paginated_get", paginate)
    monkeypatch.setattr(bot, "attach_media_to_tweets", attach)
    monkeypatch.setattr(bot, "save_state", saves)
    with pytest.raises(AttributeError):
        bot.get_quote_tweets_for_post("900")
    attach.assert_called_once_with([], None)
    assert "pagination_token" not in paginate.call_args.args[2]
    paginate.call_args.kwargs["on_invalid_cursor"]()
    saves.assert_not_called()
    failure = RuntimeError("media callback failed")
    attach.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.get_quote_tweets_for_post("900")
    assert caught.value is failure
    attach.reset_mock(side_effect=True)
    paginate.return_value = None
    with pytest.raises(AttributeError):
        bot.get_quote_tweets_for_post("900")
    attach.assert_not_called()


def test_cleanup_save_failure_preserves_state_identity_and_precedes_request(monkeypatch):
    state = {"quote_lookup_repeated_cursor_suppressions": []}
    failure = OSError("durable cleanup failed")
    request = Mock()

    def save(current, *, durable=False):
        assert current is state
        assert durable is True
        assert current["quote_lookup_repeated_cursor_suppressions"] == {}
        raise failure

    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "x_paginated_get", request)
    with pytest.raises(OSError) as caught:
        bot.get_quote_tweets_for_post("900", state)
    assert caught.value is failure
    request.assert_not_called()


def _search_quote(quote_id, parent_id, **fields):
    return {"id": str(quote_id), "author_id": "700",
            "referenced_tweets": [{"type": "quoted", "id": str(parent_id)}], **fields}


def test_combined_search_matches_five_parents_and_keeps_expansions(monkeypatch):
    parents = [str(2097428574235992387 - i) for i in range(5)]
    first = _search_quote("920", parents[0], attachments={"media_keys": ["m1"]})
    second = _search_quote("910", parents[4])
    author = {"id": "700", "description": "reader profile"}
    photo = {"media_key": "m1", "type": "photo", "url": "https://example.invalid/photo.jpg"}
    malformed = [
        _search_quote("930", "999"),
        _search_quote("931", parents[0], referenced_tweets=[{"type": "retweeted", "id": "920"}, {"type": "quoted", "id": parents[0]}]),
        _search_quote("932", parents[0], referenced_tweets=None),
        _search_quote("933", parents[0], referenced_tweets=[None]),
        _search_quote("934", parents[0], referenced_tweets=[{"type": "quoted", "id": parents[0]}, {"type": "quoted", "id": parents[1]}]),
        _search_quote("bad-id", parents[0]),
    ]
    request = Mock(return_value={"data": [first, second, first, *malformed], "includes": {"users": [author], "media": [photo]}})
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {parents[0]: "legacy-cursor"}
    state["quote_lookup_repeated_cursor_suppressions"] = {parents[0]: {"cursor_sha256": "old"}}
    result = bot.get_quote_tweets_for_posts(parents, state)
    assert list(result) == parents
    assert result[parents[0]] == [first]
    assert result[parents[4]] == [second]
    assert all(result[parent] == [] for parent in parents[1:4])
    assert first["_author_user"] is author
    assert first["_attached_media"] == [photo]
    request.assert_called_once()
    path, params = request.call_args.args
    assert path == "/2/tweets/search/recent"
    assert params["query"] == "(" + " OR ".join("quotes_of_tweet_id:" + parent for parent in sorted(parents)) + ") -is:retweet"
    assert len(params["query"]) == 220
    assert params["sort_order"] == "recency"
    assert params["expansions"] == "author_id,attachments.media_keys"
    assert "referenced_tweets" in params["tweet.fields"]
    assert params["user.fields"] == "description,username,name,public_metrics"
    assert not {"since_id", "pagination_token", "start_time"} & params.keys()
    assert state["quote_lookup_pagination_tokens"] == {parents[0]: "legacy-cursor"}


def test_combined_search_empty_and_invalid_watch_ids_do_not_request(monkeypatch):
    request = Mock()
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    assert bot.get_quote_tweets_for_posts([]) == {}
    assert bot.get_quote_tweets_for_posts(["x OR from:anyone", "²", "００３", "0", "9" * 40]) == {}
    request.assert_not_called()


def test_combined_search_batches_long_watch_lists_with_bounded_queries(monkeypatch):
    parents = [str(2097428574235992387 - i) for i in range(23)]
    request = Mock(return_value={"meta": {"result_count": 0}})
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    assert bot.get_quote_tweets_for_posts(parents + parents) == {parent: [] for parent in parents}
    assert request.call_count == 3
    assert all(len(call.args[1]["query"]) <= 512 for call in request.call_args_list)
    queries = " ".join(call.args[1]["query"] for call in request.call_args_list)
    assert all(queries.count("quotes_of_tweet_id:" + parent) == 1 for parent in parents)


def test_single_quote_search_continuation_survives_reload(monkeypatch):
    monkeypatch.setattr(bot, "QUOTE_LOOKUP_MAX_PAGES_PER_POST", 1)
    first, second = [_search_quote(target, "900") for target in ("910", "911")]
    request = Mock(side_effect=[
        {"data": [first], "meta": {"next_token": "A"}},
        {"data": [second]},
    ])
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    state = bot.default_state()
    assert bot.get_quote_tweets_for_posts(["900"], state)["900"] == [first]
    state = bot.load_state()
    query = "(quotes_of_tweet_id:900) -is:retweet"
    assert state["quote_search_pagination_tokens"] == {query: "A"}
    assert bot.get_quote_tweets_for_posts(["900"], state)["900"] == [second]
    assert [call.args[1].get("pagination_token") for call in request.call_args_list] == [None, "A"]
    assert bot.load_state()["quote_search_pagination_tokens"] == {}


def test_combined_search_changed_watch_set_discards_previous_continuation(monkeypatch):
    state = bot.default_state()
    state["quote_search_pagination_tokens"] = {"(quotes_of_tweet_id:900) -is:retweet": "old"}
    saved = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kw: saved.append(copy.deepcopy(state)))
    request = Mock(return_value={"meta": {"result_count": 0}})
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    assert bot.get_quote_tweets_for_posts(["901"], state) == {"901": []}
    assert "pagination_token" not in request.call_args.args[1]
    assert saved[0]["quote_search_pagination_tokens"] == {}


def test_combined_search_invalid_saved_cursor_recovers_once(monkeypatch):
    state = bot.default_state()
    query = "(quotes_of_tweet_id:900) -is:retweet"
    state["quote_search_pagination_tokens"] = {query: "expired"}
    quote = _search_quote("910", "900")
    request = Mock(side_effect=[bot.ApiError('Invalid pagination_token', service="x", status_code=400), {"data": [quote]}])
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    assert bot.get_quote_tweets_for_posts(["900"], state) == {"900": [quote]}
    assert [call.args[1].get("pagination_token") for call in request.call_args_list] == ["expired", None]
    assert bot.load_state()["quote_search_pagination_tokens"] == {}


def test_combined_search_repeated_cursor_retains_unique_partial_quotes(monkeypatch):
    quote = _search_quote("910", "900")
    request = Mock(return_value={"data": [quote], "meta": {"next_token": "A"}})
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    state = bot.default_state()
    assert bot.get_quote_tweets_for_posts(["900"], state) == {"900": [quote]}
    assert request.call_count == 2
    assert state["quote_search_pagination_tokens"] == {}


def test_combined_search_incomplete_page_raises_without_advancing_saved_cursor(monkeypatch):
    state = bot.default_state()
    query = "(quotes_of_tweet_id:900) -is:retweet"
    state["quote_search_pagination_tokens"] = {query: "A"}
    request = Mock(return_value={"errors": [{"detail": "unavailable"}], "meta": {"next_token": "B"}})
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    with pytest.raises(bot.ApiError):
        bot.get_quote_tweets_for_posts(["900"], state)
    assert state["quote_search_pagination_tokens"] == {query: "A"}


def test_combined_search_later_batch_failure_does_not_advance_undelivered_results(monkeypatch):
    state = bot.default_state()
    parents = [str(900 + index) for index in range(11)]
    quote = _search_quote("1000", "900")
    def paginate(request, path, params, **kwargs):
        if "quotes_of_tweet_id:910" in params["query"]:
            raise bot.ApiError("Service unavailable", service="x", status_code=503)
        return {"data": [quote], "_pagination": {"next_token": "more"}}
    monkeypatch.setattr(bot, "x_paginated_get", paginate)
    save = Mock()
    monkeypatch.setattr(bot, "save_state", save)
    with pytest.raises(bot.ApiError):
        bot.get_quote_tweets_for_posts(parents, state)
    assert state["quote_search_pagination_tokens"] == {}
    save.assert_not_called()


def test_split_quote_search_resumes_without_another_combined_head(monkeypatch):
    state = bot.default_state()
    query = "(quotes_of_tweet_id:901) -is:retweet"
    state["quote_search_pagination_tokens"] = {query: "A"}
    quote = _search_quote("910", "900")
    request = Mock(side_effect=[{"data": [quote]}, {"meta": {"result_count": 0}}])
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    assert bot.get_quote_tweets_for_posts(["901", "900"], state) == {"901": [], "900": [quote]}
    assert [call.args[1]["query"] for call in request.call_args_list] == ["(quotes_of_tweet_id:900) -is:retweet", query]
    assert [call.args[1].get("pagination_token") for call in request.call_args_list] == [None, "A"]
    assert bot.load_state()["quote_search_pagination_tokens"] == {}
