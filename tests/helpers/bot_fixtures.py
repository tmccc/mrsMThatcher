"""Share opt-in runtime isolation and local bot transaction builders.

Import ``isolate_bot_runtime`` into a test module to retain its
function-scoped autouse isolation. Importing this support module alone does not
register the fixture globally or change the suite's network policy.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.protocol_activation import create_test_protocol_activation
from tests.helpers.reply_fixtures import UNIT_REPLY_REPOSITORY, patch_tweet_lookup_method
import remote_write_transport_journal as transport_journal_module


def _configure_test_x_base(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    """Install one isolated fake X origin without mutating production authority."""

    prior_record = (
        transport_journal_module._configured_x_request_install_record
    )
    assert prior_record is not None
    monkeypatch.setattr(
        transport_journal_module,
        "_configured_x_request_install_record",
        prior_record,
    )
    normalised_base = bot.normalise_base_url(base_url, require_origin=True)
    transport_journal_module._reset_configured_x_request_provider_for_tests(
        create_url=f"{normalised_base}/2/tweets",
        auth=bot.AUTH,
        timeout=bot.request_timeout(),
    )
    monkeypatch.setattr(bot, "X_BASE", normalised_base)


@pytest.fixture(autouse=True)
def isolate_bot_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport_journal_module.reset_consumed_authorities_for_tests()
    # Operational command tests model the supported post-bootstrap dispatch path.
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", tmp_path / "meme_post_receipt.json")
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
        tmp_path / "historical_context_reply_history.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        tmp_path / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        tmp_path / "historical_context_reply_outbox.json",
    )
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", tmp_path / "confirmed_reply_receipt.json")
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", tmp_path / "ambiguous_post_outcome.json")
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        tmp_path / "ambiguous_post_outcome.restart_barrier.json",
    )
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        tmp_path / bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
    )
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_SEMANTIC_GATE",
        SimpleNamespace(
            available=True,
            ledger_sha256="unit-test-ledger",
            projection_sha256="unit-test-projection",
            disposition=lambda _quote_id: None,
            reviewed_disposition=lambda _quote_id: None,
        ),
    )
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    patch_tweet_lookup_method(monkeypatch, "fetch", lambda tweet_id, **_kwargs: {"id": str(tweet_id)})
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        bot,
        "completed_research_quote_hashes",
        lambda: {
            bot.quote_text_hash(line)
            for line in Path(bot.LINES_FILE).read_text(encoding="utf-8").splitlines()
            if line.strip()
        },
    )


def quote_analysis_for_lines(lines: list[str], analyses: dict[int, dict] | None = None) -> dict:
    analyses = analyses or {}
    items: dict[str, dict] = {}
    line_index: dict[str, str] = {}
    for idx, text in enumerate(lines):
        if not text.strip():
            continue
        quote_hash = bot.quote_text_hash(text)
        line_index[str(idx + 1)] = quote_hash
        items.setdefault(
            quote_hash,
            {
                "text": bot.collapse_quote_whitespace(text),
                "quote_hash": quote_hash,
                "line_numbers": [idx + 1],
                "analysis": analyses.get(idx, {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}),
            },
        )
    return {
        "analysis_kind": "quotes",
        "schema_version": 2,
        "source": {"source_sha256": hashlib.sha256(("".join(line + "\n" for line in lines)).encode("utf-8")).hexdigest()},
        "line_index": line_index,
        "items": items,
    }


def invalid_pagination_cursor_error() -> bot.ApiError:
    """Return a representative X invalid-pagination-token response."""
    return bot.ApiError(
        (
            'X API error 400: {"errors":[{"parameters":'
            '{"pagination_token":["expired-token"]},'
            '"message":"The `pagination_token` query parameter value '
            '[expired-token] is not valid"}]}'
        ),
        service="x",
        status_code=400,
    )


def repeated_quote_cursor_suppression(
    token: str,
    *,
    detected_epoch: int,
    retry_after_epoch: int | None = None,
) -> dict[str, object]:
    """Build one canonical quote-cursor suppression test record."""
    return {
        "cursor_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "detected_epoch": detected_epoch,
        "retry_after_epoch": retry_after_epoch
        if retry_after_epoch is not None
        else detected_epoch + bot.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
    }


def image_analysis_for_paths(paths: list[Path], analyses: dict[str, dict] | None = None) -> dict:
    analyses = analyses or {}
    path_index: dict[str, str] = {}
    items: dict[str, dict] = {}
    for path in paths:
        image_hash = bot.file_sha256(path)
        path_index[path.name] = image_hash
        items[image_hash] = {
            "paths": [path.name],
            "image_hash": image_hash,
            "analysis": analyses.get(path.name, {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}),
        }
    return {"analysis_kind": "images", "schema_version": 3, "path_index": path_index, "items": items}


def configure_simple_quote_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_response: dict | None = None,
) -> tuple[set[str], set[str], dict, Path, Path, Path, Path]:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    receipt_file = tmp_path / "regular_post_receipt.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            create_response or {"data": {"id": "950001"}},
        ),
    )
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote."]))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    return set(), set(), {}, lines_used_file, images_used_file, receipt_file, lines_file


def mock_confirmed_main_post(
    kwargs: dict[str, object],
    response: dict,
) -> dict:
    """Make a main-post double cross the real durable transport lifecycle."""
    attempt = kwargs.get("prepared_main_post_attempt")
    authority = kwargs.get("prepared_transport_authority")
    source = kwargs.get("prepared_transport_source")
    if (
        isinstance(attempt, dict)
        and isinstance(authority, bot.TransportAuthority)
        and isinstance(source, bot.SourceReceiptBinding)
    ):
        armed = bot.arm_transport_transaction(
            Path(authority.journal_path),
            authority,
            mutation_authority=bot.transaction_mutation_authority(
                "focused transport arming"
            ),
        )
        bot.consume_transport_authority(
            Path(armed.journal_path),
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=source.request.payload(),
            expected_receipt_path=Path(source.receipt_path),
        )
        post_id = response.get("data", {}).get("id")
        if bot.valid_post_id(post_id):
            confirmation_epoch = bot.confirmation_epoch_after_remote_success(
                attempt
            )
            bot.confirm_transport_transaction(
                Path(armed.journal_path),
                armed,
                mutation_authority=bot.transaction_mutation_authority(
                    "focused transport confirmation"
                ),
                post_id=str(post_id),
                confirmation_epoch=confirmation_epoch,
            )
    return json.loads(json.dumps(response))


def install_receipt_bound_x_request_stub(
    monkeypatch: pytest.MonkeyPatch,
    callback: object,
) -> None:
    """Make a low-level X stub preserve the real final authority check.

    Tests which replace ``x_request`` still need to consume the exact durable
    transport authority at the point represented by the fake transport.  A
    plain response lambda would otherwise bypass the production boundary and
    make later confirmation fail for the wrong reason.
    """

    def bound_request(method: str, path: str, **kwargs: object) -> object:
        if bot.x_request_targets_tweet_create(method, path):
            authority = kwargs.get("_remote_write_authorization")
            assert isinstance(authority, bot.TransportAuthority)
            expected_receipt_path = bot.canonical_transport_receipt_path_for_lane(
                authority.lane
            )
            assert expected_receipt_path is not None
            bot.block_if_unrelated_receipt_appeared_for_tweet_transport(
                expected_receipt_path
            )
            payload = kwargs.get("json")
            assert isinstance(payload, dict)
            bot.consume_transport_authority(
                Path(authority.journal_path),
                authority,
                method=method,
                request_path="/2/tweets",
                payload=payload,
                expected_receipt_path=expected_receipt_path,
            )
        assert callable(callback)
        return callback(method, path, **kwargs)

    monkeypatch.setattr(bot, "x_request", bound_request)


def configure_simple_meme_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, Path]:
    """Configure one entirely local daily-meme transaction."""
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    return {
        "next_meme_post_epoch": 1_799_999_000,
        "posted_meme_filenames": [],
    }, receipt_file


def valid_regular_receipt(**overrides: object) -> dict:
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    receipt.update(overrides)
    return receipt


def valid_regular_receipt_v2(**overrides: object) -> dict:
    receipt = valid_regular_receipt(schema_version=2)
    receipt["quote_history_after"] = [str(receipt["quote_hash"])]
    receipt["image_history_after"] = [str(receipt["image_basename"])]
    receipt.update(overrides)
    return receipt


def schema_current_main_attempt(lane: str) -> dict:
    """Build one current-schema attempt without touching a receipt path."""
    if lane == "quote_image":
        quote_hash = bot.quote_text_hash("Good quote.")
        return bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": 7200,
                "meme_delay_seconds": 3600,
                "meme_scheduling_enabled": True,
                "meme_trigger_after_hour": 12,
                "meme_schedule_version": 2,
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [quote_hash],
                "image_history_after": ["t01.jpg"],
            },
            attempt_epoch=1_800_000_000,
        )
    return bot.build_main_post_attempt(
        lane=lane,
        text=bot.MEME_POST_TEXT,
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={"meme_basename": "001_meme.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": 2,
            "fallback_hour": 16,
            "fallback_minute": 0,
            "image_summary": "",
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )


def write_image_analysis(path: Path, analysis: dict) -> None:
    path.write_text(json.dumps(analysis), encoding="utf-8")


def _basic_quote() -> dict:
    """Build a fresh quotation with stable original-editorial scoring metadata."""

    return {
        "quote_hash": "a" * 64,
        "line_no": 12,
        "text": "Freedom and family matter.",
        "analysis": {
            "primary_topics": ["freedom", "family"],
            "secondary_topics": [],
            "tone": ["serious"],
            "visual_energy": "medium",
            "archive_image_preferences": {"visual_affinities": ["freedom", "family"]},
            "seasonality": {"hard_exclude_outside_windows": False},
        },
    }
