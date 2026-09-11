from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

import engagement_question_experiment as experiment
import mrsMThatcher2 as bot


def _treatment_envelope() -> tuple[dict, str]:
    quote = "A synthetic canonical quotation."
    question = "What follows from this argument?"
    public_text = experiment.complete_treatment_text(quote, question)
    pair_id = "pair-0123456789abcdef01234567"
    binding = {
        "schema_version": experiment.ATTEMPT_BINDING_SCHEMA_VERSION,
        "experiment_id": experiment.EXPERIMENT_ID,
        "plan_sha256": "a" * 64,
        "pair_id": pair_id,
        "pair_index": 0,
        "member_position": 1,
        "arm": "treatment",
        "publication_order": "treatment_first",
        "publication_sequence": 1,
        "approved_question_sha256": experiment.sha256_text(question),
        "question_present": True,
        "canonical_quote_sha256": experiment.sha256_text(quote),
        "public_text_sha256": experiment.sha256_text(public_text),
        "expected_transition": {
            "confirmed_publication_count_before": 0,
            "completed_pair_count_before": 0,
            "treatment_publication_count_before": 0,
            "current_pair_index_after": 0,
            "active_pair_id_after": pair_id,
            "next_pair_member_position_after": 2,
            "completed_pair_count_after": 0,
            "status_after": "active",
        },
    }
    envelope = {
        "binding": binding,
        "canonical_quote_text": quote,
        "approved_question_body": question,
        "complete_treatment_sha256": experiment.sha256_text(public_text),
        "complete_treatment_weighted_length": experiment.x_weighted_length(
            public_text
        ),
    }
    assert bot.engagement_experiment_attempt_envelope_is_valid(
        envelope,
        public_text=public_text,
        quote_hash=binding["canonical_quote_sha256"],
    )
    return envelope, public_text


def test_fixed_experimental_image_mismatch_preserves_current_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images_used = {"already-used.jpg"}
    calls: list[dict] = []


    def mismatch(
        observed_images: set,
        _quote_choice: dict,
        _state: dict,
        **kwargs: object,
    ) -> dict:
        calls.append(dict(kwargs))
        observed_images.clear()
        raise bot.QuoteSpecificImageMismatch("no match in current cycle")

    monkeypatch.setattr(bot, "choose_matched_unused_image", mismatch)

    with pytest.raises(bot.QuoteSpecificImageMismatch):
        bot.choose_engagement_question_image(
            images_used,
            {"quote_hash": "1" * 64},
            {},
        )

    assert images_used == {"already-used.jpg"}
    assert calls == [
        {
            "force_cycle_reset": False,
            "avoid_last_image_at_cycle_boundary": True,
            "cycle_boundary_exclusions": None,
            "selection_phase": "normal",
        }
    ]


def test_experimental_media_receipt_is_durable_before_upload_and_cross_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope, _public_text = _treatment_envelope()
    image = tmp_path / "trial.png"
    image.write_bytes(b"synthetic image bytes")
    receipt_path = tmp_path / "media-upload.json"
    observations: list[str] = []

    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", receipt_path)
    monkeypatch.setattr(
        bot,
        "require_remote_operation_unpaused",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "block_if_unrelated_receipt_appeared_for_media_transport",
        lambda: None,
    )
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )
    monkeypatch.setattr(
        bot,
        "begin_confirmed_post_sigint_deferral",
        lambda: object(),
    )
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda _guard: None,
    )

    def pre_transport_validation() -> None:
        snapshot = bot.inspect_media_upload_receipt(receipt_path)
        assert snapshot is not None
        assert snapshot.document["lifecycle_state"] == "sending"
        assert snapshot.document["payload_metadata"][
            "engagement_question_experiment"
        ] == envelope
        observations.append("durable-before-upload")

    def local_request(method: str, url: str, **kwargs: object) -> object:
        assert method == "POST"
        assert url.endswith("/2/media/upload")
        form = kwargs["data"]
        assert form == {
            "media_category": "tweet_image",
            "media_type": "image/png",
        }
        assert "engagement_question_experiment" not in form
        observations.append("local-request")
        response = bot.requests.Response()
        response.status_code = 201
        response._content = json.dumps({"data": {"id": "700001"}}).encode(
            "utf-8"
        )
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(bot.requests, "request", local_request)

    assert (
        bot.upload_media(
            str(image),
            lane="quote_image",
            engagement_experiment=envelope,
            pre_transport_validation=pre_transport_validation,
        )
        == "700001"
    )
    confirmation = bot.load_confirmed_media_upload(receipt_path)
    assert confirmation is not None
    assert bot.confirmed_media_upload_experiment_envelope(confirmation) == envelope
    assert observations == ["durable-before-upload", "local-request"]

    altered = copy.deepcopy(envelope)
    altered["binding"]["public_text_sha256"] = "b" * 64
    monkeypatch.setattr(
        bot,
        "engagement_experiment_envelope_from_attempt",
        lambda _attempt: altered,
    )
    with pytest.raises(
        bot.MediaUploadReceiptError,
        match="does not match main-post attempt",
    ):
        bot.handoff_confirmed_media_upload_to_main_attempt({}, None)


def test_failed_pretransport_revalidation_aborts_without_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope, _public_text = _treatment_envelope()
    image = tmp_path / "trial.png"
    image.write_bytes(b"synthetic image bytes")
    receipt_path = tmp_path / "media-upload.json"

    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", receipt_path)
    monkeypatch.setattr(
        bot,
        "require_remote_operation_unpaused",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )
    monkeypatch.setattr(
        bot,
        "upload_media_v2",
        lambda **_kwargs: pytest.fail("pre-transport failure must forbid upload"),
    )

    def reject_after_receipt() -> None:
        assert bot.inspect_media_upload_receipt(receipt_path) is not None
        raise experiment.ExperimentValidationError("changed plan")

    with pytest.raises(experiment.ExperimentValidationError, match="changed plan"):
        bot.upload_media(
            str(image),
            lane="quote_image",
            engagement_experiment=envelope,
            pre_transport_validation=reject_after_receipt,
        )

    assert not receipt_path.exists()
    assert not bot.media_fence_path_for_receipt(receipt_path).exists()


@pytest.mark.parametrize("failure_revalidation", [2, 3])
def test_safe_handoff_authority_change_never_reaches_root_create(
    monkeypatch: pytest.MonkeyPatch,
    failure_revalidation: int,
) -> None:
    envelope, public_text = _treatment_envelope()
    quote_hash = envelope["binding"]["canonical_quote_sha256"]
    quote_choice = {
        "line_no": 0,
        "quote_hash": quote_hash,
        "text": envelope["canonical_quote_text"],
    }
    member = {
        "quote_id": quote_hash,
        "approved_question_body": envelope["approved_question_body"],
        "approved_question_sha256": envelope["binding"][
            "approved_question_sha256"
        ],
        "complete_treatment_sha256": envelope["complete_treatment_sha256"],
        "complete_treatment_weighted_length": envelope[
            "complete_treatment_weighted_length"
        ],
    }
    plan = {"plan_sha256": envelope["binding"]["plan_sha256"]}
    image_choice = {
        "image_no": 0,
        "path": "/isolated/trial.png",
        "basename": "trial.png",
        "image_source": "original",
        "score": 10.0,
    }
    state = {"engagement_question_experiment": {}}
    calls: list[str] = []
    attempts: list[dict] = []

    monkeypatch.setattr(
        bot,
        "block_if_ambiguous_remote_post",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(bot, "require_historical_context_outbox_writable", lambda: None)
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda _used: False)
    monkeypatch.setattr(
        bot,
        "engagement_question_opportunity",
        lambda *_args, **_kwargs: (plan, member, {quote_hash}),
    )
    monkeypatch.setattr(
        bot,
        "load_engagement_question_runtime_plan",
        lambda: (plan, {"entries": {}}, {}),
    )
    monkeypatch.setattr(
        bot,
        "resolve_engagement_question_quote_choice",
        lambda *_args, **_kwargs: (copy.deepcopy(quote_choice), public_text),
    )
    monkeypatch.setattr(
        experiment,
        "build_attempt_binding",
        lambda **_kwargs: copy.deepcopy(envelope["binding"]),
    )
    monkeypatch.setattr(
        bot,
        "engagement_experiment_attempt_envelope_is_valid",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        bot,
        "choose_engagement_question_image",
        lambda *_args, **_kwargs: copy.deepcopy(image_choice),
    )

    def revalidate(**_kwargs: object) -> None:
        calls.append("revalidate")
        if calls.count("revalidate") == failure_revalidation:
            raise experiment.ExperimentValidationError("changed at safe handoff")

    def local_media(_path: str, **kwargs: object) -> str:
        assert kwargs["engagement_experiment"] == envelope
        validator = kwargs["pre_transport_validation"]
        assert callable(validator)
        validator()
        calls.append("upload")
        return "700002"

    monkeypatch.setattr(
        bot,
        "revalidate_or_invalidate_engagement_question_publication",
        revalidate,
    )
    monkeypatch.setattr(bot, "upload_media", local_media)
    monkeypatch.setattr(
        bot,
        "write_main_post_attempt",
        lambda attempt: attempts.append(copy.deepcopy(attempt)),
    )
    monkeypatch.setattr(
        bot,
        "prepare_main_tweet_transport",
        lambda _attempt: pytest.fail("changed authority must forbid transport"),
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail("changed authority must forbid root create"),
    )

    with pytest.raises(
        experiment.ExperimentValidationError,
        match="changed at safe handoff",
    ):
        bot.post_random_quote(set(), set(), state)

    assert calls == [
        "revalidate",
        "upload",
        *(["revalidate"] if failure_revalidation == 2 else ["revalidate", "revalidate"]),
    ]
    assert len(attempts) == int(failure_revalidation == 3)


def test_notification_save_failure_keeps_oldest_item_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "schema_version": experiment.NOTIFICATION_SCHEMA_VERSION,
        "experiment_id": experiment.EXPERIMENT_ID,
        "plan_sha256": "a" * 64,
        "post_id": "700003",
        "pair_id": "pair-0123456789abcdef01234567",
        "treatment_number": 1,
        "target_treatment_count": experiment.TARGET_TREATMENT_COUNT,
        "published_epoch": 1_788_086_400,
        "quote_excerpt": "A synthetic canonical quotation.",
        "question": "What follows from this argument?",
        "post_url": "https://x.com/MrsMThatcher/status/700003",
    }
    identity = {
        "post_id": document["post_id"],
        "document_sha256": experiment.canonical_sha256(document),
        "delivered": False,
        "document": document,
    }
    experiment_state = {
        "treatment_notification_identities": [copy.deepcopy(identity)]
    }
    state = {"engagement_question_experiment": experiment_state}
    output = tmp_path / "notification.json"

    monkeypatch.setattr(
        bot,
        "engagement_question_notification_output_path",
        str(output),
    )
    monkeypatch.setattr(
        experiment,
        "pending_treatment_notification",
        lambda _state: copy.deepcopy(identity),
    )

    def mark_delivered(observed: dict, post_id: str) -> bool:
        assert post_id == "700003"
        observed["treatment_notification_identities"][0]["delivered"] = True
        return True

    monkeypatch.setattr(experiment, "mark_notification_delivered", mark_delivered)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert bot.publish_pending_engagement_question_notification(state) is False
    assert experiment_state["treatment_notification_identities"] == [identity]
    assert output.exists()


def test_notification_queue_does_not_overwrite_before_two_sensor_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "schema_version": experiment.NOTIFICATION_SCHEMA_VERSION,
        "experiment_id": experiment.EXPERIMENT_ID,
        "plan_sha256": "a" * 64,
        "post_id": "700005",
        "pair_id": "pair-0123456789abcdef01234567",
        "treatment_number": 2,
        "target_treatment_count": experiment.TARGET_TREATMENT_COUNT,
        "published_epoch": 1_788_090_000,
        "quote_excerpt": "A second synthetic canonical quotation.",
        "question": "What else follows from this argument?",
        "post_url": "https://x.com/MrsMThatcher/status/700005",
    }
    identity = {
        "post_id": document["post_id"],
        "document_sha256": experiment.canonical_sha256(document),
        "delivered": False,
        "document": document,
    }
    experiment_state = {
        "treatment_notification_identities": [copy.deepcopy(identity)]
    }
    state = {"engagement_question_experiment": experiment_state}
    output = tmp_path / "notification.json"
    prior_document = {"post_id": "700004", "observation": "prior"}
    bot.atomic_write_json(output, prior_document, durable=True)
    output_mtime = os.lstat(output).st_mtime

    monkeypatch.setattr(
        bot,
        "engagement_question_notification_output_path",
        str(output),
    )
    monkeypatch.setattr(
        experiment,
        "pending_treatment_notification",
        lambda _state: copy.deepcopy(identity),
    )
    monkeypatch.setattr(
        experiment,
        "mark_notification_delivered",
        lambda *_args: pytest.fail("a paced notification must remain pending"),
    )
    monkeypatch.setattr(
        bot,
        "now_epoch",
        lambda: int(output_mtime)
        + bot.ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS
        - 1,
    )

    assert bot.publish_pending_engagement_question_notification(state) is False
    assert json.loads(output.read_text(encoding="utf-8")) == prior_document
    assert experiment_state["treatment_notification_identities"] == [identity]

    def mark_delivered(observed: dict, post_id: str) -> bool:
        assert post_id == "700005"
        observed["treatment_notification_identities"][0]["delivered"] = True
        return True

    monkeypatch.setattr(experiment, "mark_notification_delivered", mark_delivered)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "now_epoch",
        lambda: int(output_mtime)
        + bot.ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS
        + 1,
    )

    assert bot.publish_pending_engagement_question_notification(state) is True
    assert json.loads(output.read_text(encoding="utf-8")) == document
    assert experiment_state["treatment_notification_identities"][0]["delivered"] is True
