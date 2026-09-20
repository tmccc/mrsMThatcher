"""Own conversational reply receipt validation and in-memory projections.

ReplyReceiptValues binds current identity, draft and time boundaries without
retaining caller state; canonical receipt encoding comes from its inert owner.
Current and frozen recovery validation dispatch through owned methods,
preserving shallow references, source lineage, timing rules and exact exception
scopes. Durable receipt I/O, retirement authority and confirmed-state application
stay with their existing owners. Import and construction perform no runtime access.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from mrs_bot_durable_json_io import canonical_atomic_json_bytes


@dataclass(frozen=True)
class ReplyReceiptValues:
    """Validate and project receipt values using current external boundaries."""

    bounded_tweet_id_value: Callable
    valid_string_post_id: Callable
    receipt_int: Callable
    valid_receipt_epoch: Callable
    safe_reply_cap_date_str: Callable
    legacy_draft_is_valid: Callable
    draft_is_valid: Callable
    legacy_tested_strategy_version: str
    legacy_ai_first_strategy_version: str
    now_epoch: Callable
    reply_cap_date_str: Callable
    log: logging.Logger
    invalid_receipt: type[Exception]

    def pagination_is_valid(self, value: object) -> bool:
        """Validate the exact mention continuation bound to a reply receipt."""
        if not isinstance(value, dict):
            return False
        if set(value) != {"base_since_id", "next_token"}:
            return False
        base_since_id = value.get("base_since_id")
        next_token = value.get("next_token")
        if (
            not isinstance(base_since_id, str)
            or self.bounded_tweet_id_value(base_since_id, allow_empty=True) is None
        ):
            return False
        if (
            not isinstance(next_token, str)
            or not next_token
            or next_token != next_token.strip()
            or any(character.isspace() for character in next_token)
        ):
            return False
        return True

    def validate(
        self,
        data: dict,
        *,
        lifecycle_state: str,
        legacy_recovery: bool = False,
    ) -> bool:
        """Validate one current receipt or a frozen lifecycle-recovery receipt."""
        if not isinstance(data, dict):
            return False
        if lifecycle_state not in {"sending", "confirmed"}:
            return False
        schema_version = data.get("schema_version")
        if type(schema_version) is not int or schema_version not in {2, 3, 4}:
            return False
        if schema_version == 2:
            if lifecycle_state != "confirmed" or "lifecycle_state" in data:
                return False
        elif data.get("lifecycle_state") != lifecycle_state:
            return False
        if not self.valid_string_post_id(data.get("target_id")):
            return False
        if lifecycle_state == "confirmed" and not self.valid_string_post_id(
            data.get("reply_post_id")
        ):
            return False
        if lifecycle_state == "sending" and "reply_post_id" in data:
            return False
        author_id = data.get("author_id")
        if not self.valid_string_post_id(author_id):
            return False
        source = data.get("candidate_source")
        if type(source) is not str:
            return False
        if source not in {"mention", "hot_post_reply", "quote_tweet"}:
            return False
        if not self._timing_is_valid(
            data, schema_version=schema_version, lifecycle_state=lifecycle_state, source=source,
        ):
            return False
        if "mention_pagination" in data:
            mention_pagination = data.get("mention_pagination")
            if schema_version not in {3, 4} or source != "mention":
                return False
            if not self.pagination_is_valid(mention_pagination):
                return False
        text = data.get("reply_text")
        if not isinstance(text, str) or not text:
            return False
        conversation_id = data.get("conversation_id")
        if not self.valid_string_post_id(conversation_id):
            return False
        if schema_version in {2, 3}:
            daily_reply_date = data.get("daily_reply_date")
            if daily_reply_date is not None and not isinstance(daily_reply_date, str):
                return False
            daily_quote_reply_date = data.get("daily_quote_reply_date")
            if daily_quote_reply_date is not None and not isinstance(
                daily_quote_reply_date,
                str,
            ):
                return False
        original_post_id = data.get("original_post_id")
        if original_post_id is not None and isinstance(original_post_id, (dict, list)):
            return False
        context = data.get("reply_context")
        if not isinstance(context, dict):
            return False
        if (
            type(context.get("target_id")) is not str
            or type(context.get("thread_id")) is not str
            or context.get("target_id") != data["target_id"]
            or context.get("thread_id") != conversation_id
            or context.get("lane") != source
        ):
            return False
        if not legacy_recovery and (
            type(context.get("target_author_id")) is not str
            or context.get("target_author_id") != author_id
        ):
            return False
        if source == "quote_tweet":
            quoted_post = context.get("quoted_post")
            if (
                not self.valid_string_post_id(original_post_id)
                or not isinstance(quoted_post, dict)
                or type(quoted_post.get("post_id")) is not str
                or quoted_post.get("post_id") != original_post_id
            ):
                return False
        elif original_post_id is not None:
            return False
        if legacy_recovery:
            try:
                legacy_draft_is_valid = self.legacy_draft_is_valid(
                    data,
                    text,
                )
            except (
                IndexError,
                KeyError,
                OverflowError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                return False
            if not legacy_draft_is_valid:
                return False
        elif not self.draft_is_valid(data, text):
            return False
        clarification = data.get("clarification_reply")
        if clarification is not None:
            if source not in {"mention", "hot_post_reply"} or not isinstance(clarification, dict):
                return False
            if set(clarification) != {
                "thread_id", "prior_bot_reply_id", "original_question_id", "trigger",
            }:
                return False
            if any(
                not self.valid_string_post_id(clarification.get(field))
                for field in ("thread_id", "prior_bot_reply_id", "original_question_id")
            ):
                return False
            if clarification.get("trigger") not in {"explicit_correction", "restated_question"}:
                return False
            if clarification.get("thread_id") != conversation_id:
                return False
            if legacy_recovery:
                draft = data.get("ai_reply_draft")
                if (
                    isinstance(draft, dict)
                    and draft.get("strategy_version")
                    in {
                        self.legacy_tested_strategy_version,
                        self.legacy_ai_first_strategy_version,
                    }
                    and draft.get("mode") != "direct_factual_answer"
                ):
                    return False
        if schema_version == 4 and lifecycle_state == "confirmed":
            return self._source_lineage_is_valid(data, legacy_recovery=legacy_recovery)
        return True

    def _timing_is_valid(
        self, data: dict, *, schema_version: int, lifecycle_state: str, source: str,
    ) -> bool:
        """Check receipt time and schema-v4 attempt/confirmation bucket agreement."""
        reply_epoch = self.receipt_int(data.get("reply_epoch"))
        if reply_epoch is None or not self.valid_receipt_epoch(reply_epoch):
            return False
        if schema_version == 4:
            attempt_epoch = self.receipt_int(data.get("attempt_epoch"))
            if attempt_epoch is None or not self.valid_receipt_epoch(attempt_epoch):
                return False
            if lifecycle_state == "sending":
                if "confirmation_epoch" in data or reply_epoch != attempt_epoch:
                    return False
                effective_epoch = attempt_epoch
            else:
                confirmation_epoch = self.receipt_int(data.get("confirmation_epoch"))
                if (
                    confirmation_epoch is None
                    or not self.valid_receipt_epoch(confirmation_epoch)
                    or confirmation_epoch < attempt_epoch
                    or reply_epoch != confirmation_epoch
                ):
                    return False
                effective_epoch = confirmation_epoch
            expected_date = self.safe_reply_cap_date_str(effective_epoch)
            if expected_date is None or data.get("daily_reply_date") != expected_date:
                return False
            if source == "quote_tweet":
                if data.get("daily_quote_reply_date") != expected_date:
                    return False
            elif "daily_quote_reply_date" in data:
                return False
        return True

    def _source_lineage_is_valid(self, data: dict, *, legacy_recovery: bool) -> bool:
        """Check the exact sending source bound to a schema-v4 confirmation."""
        if legacy_recovery and "source_receipt_sha256" not in data:
            return False
        if "source_receipt_sha256" not in data:
            return True
        try:
            source_receipt = self.sending_from_confirmed(data)
        except (TypeError, ValueError):
            return False
        source_is_valid = (
            self.legacy_sending_is_valid(source_receipt)
            if legacy_recovery
            else self.sending_is_valid(source_receipt)
        )
        if (
            not source_is_valid
            or hashlib.sha256(
                canonical_atomic_json_bytes(source_receipt)
            ).hexdigest()
            != data.get("source_receipt_sha256")
        ):
            return False
        return True

    def sending_from_confirmed(self, confirmed_receipt: dict) -> dict:
        """Reconstruct the exact schema-v4 pre-transport reply receipt.

        Legacy confirmed receipts intentionally lack ``source_receipt_sha256`` and
        remain readable for local reconciliation, but cannot use this function to
        retire a current transport journal.
        """

        if (
            not isinstance(confirmed_receipt, dict)
            or type(confirmed_receipt.get("schema_version")) is not int
            or confirmed_receipt.get("schema_version") != 4
            or confirmed_receipt.get("lifecycle_state") != "confirmed"
            or type(confirmed_receipt.get("source_receipt_sha256")) is not str
            or not re.fullmatch(
                r"[0-9a-f]{64}", confirmed_receipt["source_receipt_sha256"]
            )
        ):
            raise ValueError(
                "confirmed conversational receipt lacks exact source lineage"
            )
        attempt_epoch = self.receipt_int(confirmed_receipt.get("attempt_epoch"))
        # ``reply_text`` can be an ``AIReply`` string subclass whose constructor
        # requires provenance arguments.  No nested value is mutated here, so a
        # shallow outer copy preserves exact content without trying to reconstruct
        # that immutable subclass.
        source = dict(confirmed_receipt)
        source.pop("reply_post_id", None)
        source.pop("confirmation_epoch", None)
        source.pop("source_receipt_sha256", None)
        source["lifecycle_state"] = "sending"
        if attempt_epoch is None:
            raise ValueError("confirmed conversational receipt lacks attempt time")
        attempt_date = self.safe_reply_cap_date_str(attempt_epoch)
        if attempt_date is None:
            raise ValueError("confirmed conversational attempt time is invalid")
        source["reply_epoch"] = attempt_epoch
        source["daily_reply_date"] = attempt_date
        if source.get("candidate_source") == "quote_tweet":
            source["daily_quote_reply_date"] = attempt_date
        else:
            source.pop("daily_quote_reply_date", None)
        return source

    def confirmed_is_valid(self, data: dict) -> bool:
        """Return whether a confirmed-reply receipt is internally consistent."""
        return self.validate(
            data,
            lifecycle_state="confirmed",
        )

    def sending_is_valid(self, data: dict) -> bool:
        """Return whether a pre-send conversational-reply receipt is complete."""
        return self.validate(
            data,
            lifecycle_state="sending",
        )

    def legacy_confirmed_is_valid(self, data: dict) -> bool:
        """Accept a frozen draft only for local recovery after remote confirmation."""

        return self.validate(
            data,
            lifecycle_state="confirmed",
            legacy_recovery=True,
        )

    def legacy_sending_is_valid(self, data: dict) -> bool:
        """Recognise a frozen sending receipt as a barrier, never send authority."""

        return self.validate(
            data,
            lifecycle_state="sending",
            legacy_recovery=True,
        )

    def prepare_sending_template(self, receipt_template: dict, *, lane: str) -> dict:
        """Check current send authority and shallow-copy its reviewed receipt values."""
        if "reply_post_id" in receipt_template:
            raise ValueError("reply receipt template must not contain reply_post_id")
        if (
            type(receipt_template.get("schema_version")) is not int
            or receipt_template.get("schema_version") != 4
        ):
            raise RuntimeError(
                "Conversational X writes require a current schema-v4 source receipt"
            )
        # The reply text may be an ``AIReply`` string subclass whose constructor
        # requires provenance arguments, so ``deepcopy`` cannot reconstruct it.
        # Callers have already copied every mutable nested payload placed in the
        # template; a fresh outer mapping is sufficient and preserves the exact
        # reviewed string object for draft validation.
        receipt_template = dict(receipt_template)
        if (
            not self.sending_is_valid(receipt_template)
            or str(receipt_template.get("candidate_source") or "") != str(lane)
        ):
            raise RuntimeError(
                "Refusing conversational X write with an invalid reply receipt template"
            )
        return receipt_template

    def bind_attempt(self, receipt_template: dict) -> dict:
        """Bind a schema-v4 reply template to its immediately pre-send time."""
        if (
            not isinstance(receipt_template, dict)
            or type(receipt_template.get("schema_version")) is not int
            or receipt_template.get("schema_version") != 4
            or receipt_template.get("lifecycle_state") != "sending"
        ):
            raise RuntimeError("A reply attempt time can only bind a schema-v4 sending template")
        timing_fields = {
            "attempt_epoch",
            "confirmation_epoch",
            "reply_epoch",
            "daily_reply_date",
            "daily_quote_reply_date",
        }
        if timing_fields.intersection(receipt_template):
            raise RuntimeError("Reply attempt template already contains timing fields")
        attempt_epoch = self.now_epoch()
        attempt_date = self.reply_cap_date_str(attempt_epoch)
        prepared = {
            **receipt_template,
            "attempt_epoch": attempt_epoch,
            "reply_epoch": attempt_epoch,
            "daily_reply_date": attempt_date,
        }
        if receipt_template.get("candidate_source") == "quote_tweet":
            prepared["daily_quote_reply_date"] = attempt_date
        if not self.sending_is_valid(prepared):
            raise RuntimeError("Internal error: prepared reply attempt failed validation")
        return prepared

    def confirmed_from_sending(
        self,
        sending_receipt: dict,
        *,
        reply_post_id: str,
        confirmation_epoch: int,
    ) -> dict:
        """Build the confirmed form without mutating its durable sending input."""
        confirmed = {
            **sending_receipt,
            "lifecycle_state": "confirmed",
            "reply_post_id": str(reply_post_id),
        }
        if sending_receipt.get("schema_version") == 4:
            confirmed_date = self.reply_cap_date_str(confirmation_epoch)
            confirmed.update(
                {
                    "confirmation_epoch": confirmation_epoch,
                    "reply_epoch": confirmation_epoch,
                    "daily_reply_date": confirmed_date,
                }
            )
            if sending_receipt.get("candidate_source") == "quote_tweet":
                confirmed["daily_quote_reply_date"] = confirmed_date
            confirmed["source_receipt_sha256"] = hashlib.sha256(
                canonical_atomic_json_bytes(sending_receipt)
            ).hexdigest()
        return confirmed

    def observed_confirmation_epoch(
        self,
        sending_receipt: dict,
        observed_epoch: int | None = None,
    ) -> int:
        """Return a conservative monotonic wall time after remote confirmation."""
        if observed_epoch is None:
            observed_epoch = self.now_epoch()
        observed_epoch = int(observed_epoch)
        if sending_receipt.get("schema_version") != 4:
            return observed_epoch
        attempt_epoch = self.receipt_int(sending_receipt.get("attempt_epoch"))
        if attempt_epoch is None or observed_epoch >= attempt_epoch:
            return observed_epoch
        self.log.warning(
            "Wall clock moved backward during conversational reply creation; "
            "using durable attempt epoch as conservative confirmation time "
            "attempt_epoch=%s observed_epoch=%s",
            attempt_epoch,
            observed_epoch,
        )
        return attempt_epoch

    def confirmation_epoch(self, receipt: dict) -> int:
        """Return the best available confirmed time for a reply receipt."""
        if receipt.get("schema_version") == 4:
            confirmation_epoch = self.receipt_int(receipt.get("confirmation_epoch"))
            if confirmation_epoch is None:
                raise self.invalid_receipt(
                    "Schema-v4 confirmed reply receipt lacks a confirmation epoch"
                )
            return confirmation_epoch
        reply_epoch = self.receipt_int(receipt.get("reply_epoch"))
        if reply_epoch is None:
            raise self.invalid_receipt(
                "Legacy confirmed reply receipt lacks its best-known reply epoch"
            )
        return reply_epoch
