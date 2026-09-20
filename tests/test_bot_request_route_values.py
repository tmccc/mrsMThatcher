from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_request_route_values as route_values
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import patch_reply_owner_method


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from types import ModuleType
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('request-route values import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_request_route_values':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_request_route_values
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


def test_adapters_forward_current_dependencies_arguments_references_and_errors(monkeypatch):
    assert bot.exact_x_create_route is route_values.exact_x_create_route
    for name, count in (
        ("normalise_base_url", 2), ("endpoint_host", 0),
        ("endpoint_is_loopback", 0),
        ("frozen_strict_json_object", 1),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(route_values, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(bot, "_request_route_values", SimpleNamespace(**{name: owner}))
            required = {key: object() for key, param in public.items()
                        if param.kind == param.KEYWORD_ONLY and param.default is param.empty}
            for options in (required, {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, "TEST_MODE" if key == "test_mode" else key, value)
                assert adapter(*args, **options) is result
                expected = {key: options.get(key, param.default) for key, param in public.items() if param.kind == param.KEYWORD_ONLY} | current
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_route_owner_and_adapters_keep_current_capabilities_and_reference_contract(monkeypatch):
    names = {"primary_base": "X_BASE", "upload_base": "X_UPLOAD_BASE",
             "requests": "requests", "ambiguous_outcome": "AmbiguousRemotePostOutcome"}
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in names}
        for field, name in names.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._x_request_routes_owner()
        snapshots.append(owner)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
    assert snapshots[0] is not snapshots[1]
    for name, method in {'x_request_base_url': 'base_url', 'normalised_prepared_x_request_path': 'normalised_path', 'x_request_targets_tweet_create': 'targets_tweet_create', 'x_request_targets_media_upload': 'targets_media_upload', 'prepared_x_create_route': 'prepared_route'}.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter)
        owned = inspect.signature(getattr(route_values.XRequestRoutes, method))
        assert list(public.parameters.values()) == list(owned.parameters.values())[1:]
        assert public.return_annotation == owned.return_annotation
        owner = Mock(spec=route_values.XRequestRoutes)
        factory = Mock(return_value=owner)
        with monkeypatch.context() as patch:
            patch.setattr(bot, "_x_request_routes_owner", factory)
            implementation = getattr(owner, method)
            result, method_value, path = object(), object(), object()
            implementation.return_value = result
            assert adapter(method_value, path) is result
            factory.assert_called_once_with()
            assert implementation.call_args.args[0] is method_value
            assert implementation.call_args.args[1] is path
            failure = RuntimeError("owned route failure")
            implementation.side_effect = failure
            with pytest.raises(RuntimeError) as caught:
                adapter(method_value, path)
            assert caught.value is failure


def test_origin_delegates_before_conversion_and_provider_validation_keeps_order(monkeypatch):
    failure = TypeError("native string failure")

    class Raw:
        def __str__(self):
            raise failure

    raw, result = Raw(), object()
    origin = Mock(return_value=result)
    parser = Mock(wraps=bot.urlsplit)
    monkeypatch.setattr(bot, "_normalise_x_origin_before_runtime_configuration", origin)
    monkeypatch.setattr(route_values, "urlsplit", parser)
    assert bot.normalise_base_url(raw, require_origin=True) is result
    origin.assert_called_once_with(raw)
    parser.assert_not_called()
    with pytest.raises(TypeError) as caught:
        bot.normalise_base_url(raw)
    assert caught.value is failure
    parser.assert_not_called()
    assert bot.normalise_base_url(" HTTPS://LOCALHOST:443/v1/// ") == "HTTPS://LOCALHOST:443/v1"
    parser.assert_called_once_with("HTTPS://LOCALHOST:443/v1///")
    for candidate, message in (
        ("https://localhost/\x01", "control characters"),
        ("ftp://user@localhost:bad/v1?query=yes", "invalid port"),
        ("ftp://user@localhost/v1?query=yes", "must use http or https"),
        ("http://user@localhost/v1?query=yes", "without user information"),
        ("http://localhost/v1?query=yes", "must not contain a query or fragment"),
    ):
        with pytest.raises(ValueError, match=message) as caught:
            bot.normalise_base_url(candidate)
        if message == "invalid port":
            assert isinstance(caught.value.__cause__, ValueError)
    parser.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.normalise_base_url("http://localhost/v1")
    assert caught.value is failure
    origin.assert_called_once_with(raw)


def test_endpoint_host_policy_uses_no_resolution_and_keeps_exception_boundaries(monkeypatch):
    for url, expected in (
        ("http://LOCALHOST:9", True),
        ("http://localhost.localdomain", True),
        ("http://service.localhost", True),
        ("http://127.0.0.1:9", True),
        ("http://[::1]:9", True),
        ("http://localhost.", False),
        ("http://service.localhost.localdomain", False),
        ("http://127.0.0.1%40.attacker.invalid", False),
    ):
        assert bot.endpoint_is_loopback(url) is expected
    assert bot.endpoint_host("http://LOCALHOST:9") == "localhost"
    parser = Mock(side_effect=RuntimeError("parser failure"))
    monkeypatch.setattr(route_values, "urlsplit", parser)
    assert bot.endpoint_host(object()) == ""
    interrupted = KeyboardInterrupt("parser interrupted")
    parser.side_effect = interrupted
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.endpoint_host("ignored")
    assert caught.value is interrupted

    host = Mock(return_value="service.localhost")
    address = Mock(side_effect=ValueError("invalid IP"))
    monkeypatch.setattr(route_values, "endpoint_host", host)
    monkeypatch.setattr(route_values, "ipaddress", SimpleNamespace(ip_address=address))
    value = object()
    assert bot.endpoint_is_loopback(value) is True
    host.assert_called_once_with(value)
    address.assert_not_called()
    host.return_value = "unresolved.invalid"
    assert bot.endpoint_is_loopback(value) is False
    address.assert_called_once_with("unresolved.invalid")
    failure = TypeError("native IP failure")
    address.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.endpoint_is_loopback(value)
    assert caught.value is failure


def test_prepared_path_uses_current_callbacks_with_four_decodes_in_original_order(monkeypatch):
    trace = Mock()
    trace.base.return_value = "http://127.0.0.1:9"
    trace.Request.return_value = SimpleNamespace(prepare=trace.prepare)
    trace.prepare.return_value = SimpleNamespace(url="prepared-url")
    encoded = r"//%2525252532\branch\..\tweets///"
    trace.split.return_value = SimpleNamespace(path=encoded)
    trace.decode.side_effect = bot.unquote
    trace.normpath.side_effect = bot.posixpath.normpath
    trace.sub.side_effect = bot.re.sub
    patch_reply_owner_method(monkeypatch, route_values.XRequestRoutes, "base_url", trace.base)
    monkeypatch.setattr(bot, "requests", SimpleNamespace(
        Request=trace.Request, RequestException=bot.requests.RequestException,
    ))
    monkeypatch.setattr(route_values, "urlsplit", trace.split)
    monkeypatch.setattr(route_values, "unquote", trace.decode)
    monkeypatch.setattr(route_values, "posixpath", SimpleNamespace(normpath=trace.normpath))
    monkeypatch.setattr(route_values, "re", SimpleNamespace(sub=trace.sub))
    assert bot.normalised_prepared_x_request_path("post", "/candidate") == "/%32/tweets"
    assert trace.mock_calls == [
        call.base("post", "/candidate"),
        call.Request(method="POST", url="http://127.0.0.1:9/candidate"),
        call.prepare(), call.split("prepared-url"), call.decode(encoded),
        call.decode(r"//%25252532\branch\..\tweets///"),
        call.decode(r"//%252532\branch\..\tweets///"),
        call.decode(r"//%2532\branch\..\tweets///"),
        call.normpath("//%32/branch/../tweets///"),
        call.sub(r"/+", "/", "//%32/tweets"),
    ]
    trace.reset_mock()
    trace.split.return_value.path = "2/tweets"
    assert bot.normalised_prepared_x_request_path("POST", "/candidate") == "/2/tweets"
    trace.decode.assert_called_once_with("2/tweets")
    trace.normpath.assert_called_once_with("2/tweets")
    trace.sub.assert_called_once_with(r"/+", "/", "2/tweets")


def test_preparation_uses_current_exception_and_preserves_native_failures(monkeypatch):
    class CurrentOutcome(bot.AmbiguousRemotePostOutcome):
        pass

    class RequestFailure(Exception):
        pass

    prepare, parser, base = Mock(), Mock(), Mock(return_value="http://127.0.0.1:9")
    factory = Mock(return_value=SimpleNamespace(prepare=prepare))
    monkeypatch.setattr(bot, "AmbiguousRemotePostOutcome", CurrentOutcome)
    monkeypatch.setattr(bot, "requests", SimpleNamespace(Request=factory, RequestException=RequestFailure))
    monkeypatch.setattr(route_values, "urlsplit", parser)
    patch_reply_owner_method(monkeypatch, route_values.XRequestRoutes, "base_url", base)
    failure = RequestFailure("preparation failed")
    prepare.side_effect = failure
    with pytest.raises(CurrentOutcome, match="could not be prepared safely") as caught:
        bot.normalised_prepared_x_request_path("post", "/candidate")
    assert caught.value.__cause__ is failure
    assert (caught.value.service, caught.value.request_method, caught.value.request_path) == ("x", "POST", "/candidate")
    parser.assert_not_called()
    native = TypeError("native preparation failure")
    prepare.side_effect = native
    with pytest.raises(TypeError) as caught:
        bot.normalised_prepared_x_request_path("post", "/candidate")
    assert caught.value is native
    prepare.side_effect = None
    for url in (None, "", 4):
        prepare.return_value = SimpleNamespace(url=url)
        with pytest.raises(CurrentOutcome, match="returned no usable URL") as caught:
            bot.normalised_prepared_x_request_path("post", "/candidate")
        assert caught.value.__cause__ is None
    parser.assert_not_called()
    prepare.return_value = object()
    with pytest.raises(AttributeError):
        bot.normalised_prepared_x_request_path("post", "/candidate")
    prepare.return_value = SimpleNamespace(url="prepared-url")
    parser.side_effect = native
    with pytest.raises(TypeError) as caught:
        bot.normalised_prepared_x_request_path("post", "/candidate")
    assert caught.value is native

    class Method:
        def __str__(self):
            raise native

    base.reset_mock()
    factory.reset_mock()
    with pytest.raises(TypeError) as caught:
        bot.normalised_prepared_x_request_path(Method(), "/candidate")
    assert caught.value is native
    base.assert_not_called()
    factory.assert_not_called()


def test_route_callbacks_keep_original_references_and_short_circuit_order(monkeypatch):
    normalise = Mock(return_value="/2/tweets///")
    patch_reply_owner_method(monkeypatch, route_values.XRequestRoutes, "normalised_path", normalise)
    path = object()
    assert bot.prepared_x_create_route("GET", path) is None
    normalise.assert_not_called()
    assert bot.exact_x_create_route("post", path) is None
    assert bot.prepared_x_create_route("post", path) == "tweet"
    normalise.assert_called_once_with("post", path)
    normalise.return_value = "/2/media/upload/"
    assert bot.prepared_x_create_route("POST", path) == "media"
    callback = Mock(side_effect=["tweet", "media"])
    patch_reply_owner_method(monkeypatch, route_values.XRequestRoutes, "prepared_route", callback)
    assert bot.x_request_targets_tweet_create("method", path) is True
    assert bot.x_request_targets_media_upload("method", path) is True
    assert callback.call_args_list == [call("method", path), call("method", path)]
    failure = RuntimeError("current route failure")
    callback.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.x_request_targets_tweet_create("method", path)
    assert caught.value is failure

    spellings = Mock(side_effect=["not-tweet", "/2/media/upload"])

    class PathValue:
        def __str__(self):
            return spellings()

    assert bot.exact_x_create_route("POST", PathValue()) == "media"
    assert spellings.call_count == 2


def test_strict_json_copy_preserves_canonical_encoding_and_isolates_nested_values(monkeypatch):
    trace = Mock()
    trace.dumps.side_effect = bot.json.dumps
    trace.loads.side_effect = bot.json.loads
    monkeypatch.setattr(route_values, "json", SimpleNamespace(
        dumps=trace.dumps, loads=trace.loads, JSONDecodeError=bot.json.JSONDecodeError,
    ))
    shared = {"values": (1, "é")}
    payload = {"z": shared, "a": shared}
    result = bot.frozen_strict_json_object(payload, label="payload")
    assert trace.mock_calls == [
        call.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        call.loads('{"a":{"values":[1,"é"]},"z":{"values":[1,"é"]}}'),
    ]
    assert list(result) == ["a", "z"] and result is not payload
    assert result["a"] is not shared and result["a"] is not result["z"]
    result["a"]["values"].append(2)
    assert result["z"] == {"values": [1, "é"]}
    assert shared == {"values": (1, "é")}
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="not strict JSON") as caught:
        bot.frozen_strict_json_object({"value": float("nan")}, label="payload")
    assert isinstance(caught.value.__cause__, ValueError)


def test_strict_json_owned_codec_keeps_reference_error_order_and_chains(monkeypatch):
    class CurrentOutcome(bot.AmbiguousRemotePostOutcome):
        pass

    class DecodeFailure(Exception):
        pass

    trace = Mock()
    encoded, decoded = "encoded-current-object", {"current": []}
    trace.dumps.return_value, trace.loads.return_value = encoded, decoded
    monkeypatch.setattr(bot, "AmbiguousRemotePostOutcome", CurrentOutcome)
    monkeypatch.setattr(route_values, "json", SimpleNamespace(
        dumps=trace.dumps, loads=trace.loads, JSONDecodeError=DecodeFailure,
    ))
    with pytest.raises(CurrentOutcome, match="payload must be one JSON object"):
        bot.frozen_strict_json_object([], label="payload")
    assert trace.mock_calls == []
    payload = {"original": []}
    assert bot.frozen_strict_json_object(payload, label="payload") is decoded
    assert trace.mock_calls == [
        call.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        call.loads(encoded),
    ]
    trace.reset_mock()
    failure = TypeError("encoder failure")
    trace.dumps.side_effect = failure
    with pytest.raises(CurrentOutcome, match="payload is not strict JSON") as caught:
        bot.frozen_strict_json_object(payload, label="payload")
    assert caught.value.__cause__ is failure and caught.value.service == "x"
    trace.loads.assert_not_called()
    trace.dumps.side_effect = None
    failure = DecodeFailure("current decoder failure")
    trace.loads.side_effect = failure
    with pytest.raises(CurrentOutcome, match="payload is not strict JSON") as caught:
        bot.frozen_strict_json_object(payload, label="payload")
    assert caught.value.__cause__ is failure
    native = OverflowError("native decoder failure")
    trace.loads.side_effect = native
    with pytest.raises(OverflowError) as caught:
        bot.frozen_strict_json_object(payload, label="payload")
    assert caught.value is native
    trace.loads.side_effect, trace.loads.return_value = None, []
    with pytest.raises(CurrentOutcome, match="payload must remain one JSON object") as caught:
        bot.frozen_strict_json_object(payload, label="payload")
    assert caught.value.__cause__ is None
    assert payload == {"original": []}
