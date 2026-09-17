"""Synthetic configuration, calendar and editorial cache regressions."""
import json

import pytest

from tests.helpers.bot_runtime import bot


@pytest.mark.parametrize('day, excluded', [('02-28', False), ('02-29', False), ('03-01', True)])
def test_winter_calendar_boundary(day, excluded):
    """Winter includes leap day and excludes March."""
    analysis = {'seasonality': {
        'avoid_outside_season_or_occasion': True,
        'visible_season': 'winter',
        'occasions': [],
    }}
    observed = bot.image_is_out_of_season(analysis, day)
    assert observed is excluded


def test_cached_editorial_rows_preserve_a_new_unrepresented_baseline(tmp_path, monkeypatch):
    """Exercise real cache loading and winner application after corpus growth."""
    old = tmp_path / 't01.jpg'
    new = tmp_path / 't02.jpg'
    old.write_bytes(b'synthetic old original image')
    visible = [str(old)]
    analysis_path = tmp_path / 'editorial.json'
    analysis_path.write_text(json.dumps({
        'analysis_kind': 'synthetic', 'schema_version': 3,
        'items': {'t01.jpg': {
            'sha256': bot.file_sha256(old),
            'analysis': {'dimension_scores': {'conviction': 5}, 'overall_editorial_utility': 5.5},
        }},
    }), encoding='utf-8')
    monkeypatch.setattr(bot, 'ORIGINAL_EDITORIAL_ANALYSIS_FILE', analysis_path)
    monkeypatch.setattr(bot, 'ORIGINAL_EDITORIAL_ANALYSIS_KIND', 'synthetic')
    monkeypatch.setattr(bot, 'ORIGINAL_EDITORIAL_SCHEMA_VERSION', 3)
    monkeypatch.setattr(bot, 'ORIGINAL_EDITORIAL_DIMENSIONS', ['conviction'])
    monkeypatch.setattr(bot, '_ORIGINAL_EDITORIAL_ANALYSIS_CACHE', {})
    monkeypatch.setattr(bot, 'current_image_paths', lambda: list(visible))
    monkeypatch.setattr(bot, 'generated_image_origin_quote_hash', lambda _: None)
    monkeypatch.setattr(bot, 'ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING', True)
    loaded = bot.load_original_editorial_analysis()
    assert set(loaded) == {'t01.jpg'}
    new.write_bytes(b'synthetic newly analysed original image')
    visible.append(str(new))
    assert bot.load_original_editorial_analysis() is loaded
    baseline = {'basename': new.name, 'image_source': 'original', 'score': 100.0, 'components': {}}
    older = {'basename': old.name, 'image_source': 'original', 'score': 1.0, 'components': {}}
    selected = bot.apply_original_editorial_selection(
        {'quote_hash': 'synthetic', 'analysis': {}, 'line_no': 0},
        baseline, [baseline, older], selection_phase='normal',
    )
    assert selected is baseline


@pytest.mark.parametrize('case', ['whitespace', 'nonnumeric_id'])
def test_credential_validation_rejects_reported_bad_forms(monkeypatch, case):
    """Use only synthetic credentials to check the bootstrap validation gap."""
    for key in ('CONSUMER_KEY', 'CONSUMER_SECRET', 'ACCESS_TOKEN', 'ACCESS_SECRET'):
        monkeypatch.setattr(bot, key, ' ' if case == 'whitespace' else 'synthetic-value')
    monkeypatch.setattr(bot, 'MY_USER_ID', ' ' if case == 'whitespace' else 'not-a-snowflake')
    monkeypatch.setattr(bot, 'ENABLE_AUTO_REPLIES', False)
    monkeypatch.setattr(bot, 'single_call_reply', {'enabled': False})
    monkeypatch.setattr(bot, 'OPENAI_API_KEY', '')
    with pytest.raises(RuntimeError):
        bot.validate_production_credentials()


def test_openai_source_default_limit_is_in_shared_positive_sweep(tmp_path, monkeypatch):
    """Source defaults and local overrides share the same positive bounds."""
    candidate = dict(bot.SOURCE_DEFAULT_CONFIG_VALUES)
    assert bot.validate_runtime_config_values(candidate) == []
    candidate['MAX_OPENAI_ERRORS_PER_WINDOW'] = 0
    errors = bot.validate_runtime_config_values(candidate)
    assert any('MAX_OPENAI_ERRORS_PER_WINDOW' in error for error in errors)
    candidate['MAX_X_ERRORS_PER_WINDOW'] = 0
    assert any('MAX_X_ERRORS_PER_WINDOW' in error for error in bot.validate_runtime_config_values(candidate))
    config_file = tmp_path / 'mrsMThatcher.local.json'
    config_file.write_text(json.dumps({
        'MAX_OPENAI_ERRORS_PER_WINDOW': 0,
        'MAX_X_ERRORS_PER_WINDOW': 4,
    }), encoding='utf-8')
    monkeypatch.setattr(bot, 'LOCAL_CONFIG_FILE', config_file)
    monkeypatch.setattr(bot, 'MAX_OPENAI_ERRORS_PER_WINDOW', 3)
    monkeypatch.setattr(bot, 'MAX_X_ERRORS_PER_WINDOW', 3)
    with pytest.raises(bot.LocalConfigError, match='MAX_OPENAI_ERRORS_PER_WINDOW must be positive'):
        bot.apply_local_config()
    assert bot.MAX_OPENAI_ERRORS_PER_WINDOW == 3
    assert bot.MAX_X_ERRORS_PER_WINDOW == 3


@pytest.mark.parametrize("key", ["CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "MY_USER_ID"])
def test_each_required_credential_rejects_whitespace(monkeypatch, key):
    monkeypatch.setattr(bot, key, " \t\n")
    with pytest.raises(RuntimeError, match="Missing X credentials"):
        bot.validate_production_credentials()


def test_openai_whitespace_requirement_remains_conditional(monkeypatch):
    monkeypatch.setattr(bot, "OPENAI_API_KEY", " \t")
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "single_call_reply", {"enabled": True})
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        bot.validate_production_credentials()
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
    bot.validate_production_credentials()
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "single_call_reply", {"enabled": False})
    bot.validate_production_credentials()


def test_winter_harness_boundaries_cover_leap_and_nonleap_years(monkeypatch):
    from types import SimpleNamespace
    from quote_image_selection_harness import configured_boundaries, production_sim
    ctx = SimpleNamespace(quote_text={}, bot=SimpleNamespace(load_image_analysis=lambda: {
        "path_index": {"winter.jpg": "hash"}, "items": {"hash": {"analysis": {
            "seasonality": {"avoid_outside_season_or_occasion": True, "visible_season": "winter"}
        }}}
    }))
    monkeypatch.setattr(production_sim, "historical_image_selection", lambda _bot: ctx.bot)
    ends = [row for row in configured_boundaries(ctx, [2024, 2025]) if row["boundary_kind"] == "end"]
    assert [row["boundary_date"] for row in ends] == ["2024-02-29", "2025-02-28"]
    assert all(row["window_end"] == "02-29" for row in ends)
