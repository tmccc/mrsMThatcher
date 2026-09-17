"""Regressions for full X post text at discovery, cache and restart boundaries."""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

from tests.helpers.bot_runtime import bot, SOURCE_GET_TWEET_BY_ID
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.mention_fixtures import mention


@pytest.mark.parametrize('note', [None, {}, {'text': ''}, {'text': '   '}])
def test_short_post_fallback_keeps_text_and_entities(note):
    tweet = {'id': '100', 'text': 'A complete short post.', 'entities': {'mentions': []}}
    if note is not None:
        tweet['note_tweet'] = note
    bot.normalise_tweet_text(tweet)
    assert tweet['text'] == 'A complete short post.'
    assert tweet['entities'] == {'mentions': []}
    assert bot.tweet_text_is_complete(tweet)


def test_long_text_and_entities_keep_implicit_reply_recipient():
    full = '背景の説明。' * 100 + '最後の質問はここにあります。'
    tweet = {
        'id': '100', 'text': '@MrsMThatcher truncated',
        'entities': {'mentions': [{'id': str(bot.MY_USER_ID), 'username': 'MrsMThatcher'}]},
        'note_tweet': {'text': full, 'entities': {'urls': [{'url': 'https://example.invalid/full'}], 'mentions': []}},
    }
    before = copy.deepcopy(tweet['note_tweet'])
    bot.normalise_tweet_text(tweet)
    bot.normalise_tweet_text(tweet)
    assert tweet['text'] == full
    assert tweet['note_tweet'] == before
    assert len(tweet['entities']['mentions']) == 1
    assert tweet['entities']['urls'] == before['entities']['urls']
    assert bot.reply_target_is_directly_eligible(tweet)


def test_legacy_cache_refresh_persists_full_text_once_and_retains_local_metadata(monkeypatch):
    state = bot.default_state()
    state['tweet_cache']['100'] = {
        'id': '100', 'text': 'Incomplete prefix', 'author_id': '200',
        'conversation_id': '100', 'cached_epoch': bot.now_epoch(),
        'image_summary': 'An existing image description', 'post_type': 'author_cap_context',
    }
    full = 'Background ' * 40 + 'The British Nationality Act 1981 is the measure I mean.'
    request = Mock(return_value={'data': {'id': '100', 'text': 'Incomplete prefix', 'author_id': '200', 'note_tweet': {'text': full}}})
    monkeypatch.setattr(bot, 'x_request', request)
    monkeypatch.setattr(bot, 'get_tweet_by_id', SOURCE_GET_TWEET_BY_ID)
    first = bot.get_tweet_by_id_cached('100', state)
    assert first['text'] == full
    assert first['image_summary'] == 'An existing image description'
    assert first['post_type'] == 'author_cap_context'
    assert 'note_tweet' in request.call_args.kwargs['params']['tweet.fields'].split(',')
    saved = json.loads(bot.STATE_FILE.read_text())
    saved['tweet_cache'] = bot.normalise_tweet_cache(saved['tweet_cache'], path=bot.STATE_FILE)
    assert bot.get_tweet_by_id_cached('100', saved)['text'] == full
    assert request.call_count == 1


@pytest.mark.parametrize('failure', [None, 404, 503])
def test_legacy_cache_never_falls_back_to_incomplete_text(monkeypatch, failure):
    state = bot.default_state()
    cached = {'id': '100', 'text': 'Incomplete', 'cached_epoch': bot.now_epoch()}
    state['tweet_cache']['100'] = cached
    fetch = Mock(return_value=None)
    if failure:
        fetch.side_effect = bot.ApiError('lookup failed', service='x', status_code=failure)
    monkeypatch.setattr(bot, 'get_tweet_by_id', fetch)
    if failure:
        with pytest.raises(bot.ApiError):
            bot.get_tweet_by_id_cached('100', state)
    else:
        assert bot.get_tweet_by_id_cached('100', state) is None
    assert state['tweet_cache']['100'] == cached
    assert not bot.tweet_text_is_complete(cached)


def test_legacy_parent_refreshes_respect_network_fetch_budget(monkeypatch):
    state = bot.default_state()
    for tid, parent in [('102', '101'), ('101', '100'), ('100', None)]:
        state['tweet_cache'][tid] = {
            'id': tid, 'text': 'old', 'cached_epoch': bot.now_epoch(),
            'referenced_tweets': [{'type': 'replied_to', 'id': parent}] if parent else [],
        }
    target = {'id': '103', 'referenced_tweets': [{'type': 'replied_to', 'id': '102'}]}
    monkeypatch.setattr(bot, 'THREAD_CONTEXT_MAX_NETWORK_FETCHES', 1)
    lookup = Mock(side_effect=lambda tid, current: current['tweet_cache'][tid])
    monkeypatch.setattr(bot, 'get_tweet_by_id_cached', lookup)
    assert [row['id'] for row in bot.build_parent_chain(target, state)] == ['102']
    lookup.assert_called_once_with('102', state)


def test_legacy_pending_mention_is_refreshed_and_saved_before_return(monkeypatch):
    state = bot.default_state()
    candidate = mention(100, 200, 'Old incomplete prefix')
    candidate.pop('text_is_complete')
    state['last_seen_mention_id'] = '100'
    state['mention_pending_candidates'] = {'100': candidate}
    fresh = mention(100, 200, 'Old incomplete prefix')
    full = 'Background ' * 40 + 'Here is the actual question at the end.'
    fresh['note_tweet'] = {'text': full}
    monkeypatch.setattr(bot, 'get_tweet_by_id', Mock(return_value=fresh))
    rows = bot.get_mentions(state)
    assert len(rows) == 1 and rows[0]['text'] == full
    saved = json.loads(bot.STATE_FILE.read_text())
    assert saved['mention_pending_candidates']['100']['text'] == full
    assert saved['tweet_cache']['100']['text'] == full
    assert saved['last_seen_mention_id'] == '100'
    bot.get_mentions(saved)
    bot.get_tweet_by_id.assert_called_once_with('100', include_media=True)


@pytest.mark.parametrize('status', [404, 503])
def test_legacy_queue_handles_deleted_and_transient_lookup_failures(monkeypatch, status):
    state = bot.default_state()
    candidate = mention(100, 200)
    candidate.pop('text_is_complete')
    state['last_seen_mention_id'] = '100'
    state['mention_pending_candidates'] = {'100': candidate}
    monkeypatch.setattr(bot, 'get_tweet_by_id', Mock(side_effect=bot.ApiError('lookup failed', service='x', status_code=status, request_method='GET', request_path='/2/tweets/100')))
    if status == 503:
        with pytest.raises(bot.ApiError):
            bot.get_mentions(state)
        assert '100' in state['mention_pending_candidates']
    else:
        assert bot.get_mentions(state) == []
        assert state['mention_pending_candidates'] == {}
        assert json.loads(bot.STATE_FILE.read_text())['mention_pending_candidates'] == {}
