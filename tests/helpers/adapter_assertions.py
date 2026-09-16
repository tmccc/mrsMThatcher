"""Assertions for plain root adapters with explicitly forwarded dependencies."""

from __future__ import annotations

import inspect
from types import ModuleType
from unittest.mock import Mock

import pytest


def assert_adapters_forward_current_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bot: ModuleType,
    implementation: ModuleType,
    names: tuple[str, ...],
) -> None:
    """Check argument/result identity, refreshed dependencies and native errors.

    Only plain adapters with positional and keyword-only arguments belong here.
    Tests for grouped callbacks, default values and imports remain local.
    """

    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = (
            inspect.signature(getattr(implementation, name)).parameters.keys()
            - public.keys()
        )
        args = tuple(
            object() for parameter in public.values()
            if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        options = {
            key: object() for key, parameter in public.items()
            if parameter.kind == inspect.Parameter.KEYWORD_ONLY
        }
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(implementation, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result, name
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(
                    actual is expected for actual, expected in zip(actual_args, args)
                ), name
                expected = {**options, **current}
                assert actual_kwargs.keys() == expected.keys(), name
                assert all(
                    actual_kwargs[key] is value for key, value in expected.items()
                ), name
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure
