"""Adapt historical reply/None test fakes to the production typed evaluator.

Only old test doubles use this adapter. A missing model count means one reserved
evaluation, matching those tests' original cycle contract; explicit zero stays
zero. Missing decisions become operational failures, never editorial no-reply.
New evaluator tests should return PipelineResult directly to check real metadata.
"""

from unittest.mock import Mock

from single_call_reply import PipelineResult


def legacy_reply_evaluator(callback):
    """Preserve old fake side effects and observations at the new typed seam."""
    def evaluate(*args, **kwargs):
        outcome = {}
        reply = callback(*args, **kwargs, evaluation_outcome=outcome)
        if isinstance(reply, PipelineResult):
            return reply
        return PipelineResult(
            status=outcome.get("status", "reply" if reply is not None else "operational_failure"),
            reason=outcome.get("reason", "legacy_test_reply" if reply is not None else "unspecified_test_failure"),
            reply=reply,
            reason_code=outcome.get("reason_code"),
            reply_kind=outcome.get("reply_kind"),
            error_category=outcome.get("error_category"),
            model_call_count=outcome.get("model_call_count", 1),
        )

    return Mock(side_effect=evaluate)
