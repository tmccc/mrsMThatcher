"""Validate endpoint and X request-route values through current root dependencies.

Nine explicit root adapters supply current parsers, modules, callbacks, configured
bases and exception authority on each call; the exact-route helper is a direct
alias. Original bodies preserve origin delegation, hostname policy, literal
upload routing, prepared-route classification and strict JSON copy boundaries.
Request preparation never sends; authentication, timeouts, transaction transport
and configuration remain with their existing owners. No constants or classes
move. This standard-library-only owner retains no callbacks, configuration,
clients or state and performs no import-time file, environment, provider or RNG
work.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

from provider_endpoint_policy import validate_provider_endpoint


def normalise_base_url(
    raw: str,
    *,
    require_origin: bool = False,
    provider: str | None = None,
    test_mode: bool = False,
    _normalise_x_origin_before_runtime_configuration: Callable[[object], str],
    urlsplit: Callable,
    urlunsplit: Callable,
) -> str:
    """Return one validated API base or fail during configuration.

    Route classification is performed against paths which this module appends
    itself.  A configured path prefix, query, fragment or user-info component
    could make the literal route and the prepared on-wire route disagree, so
    the X request and upload bases must be origins. Versioned OpenAI and xAI
    bases remain usable through this generic normalizer. Credential-bearing
    configuration must pass its explicit provider to prevent cross-provider
    credential disclosure.
    """

    if require_origin:
        return _normalise_x_origin_before_runtime_configuration(raw)

    value = str(raw or "").strip()
    if not value or any(ord(character) < 0x20 for character in value):
        raise ValueError("API base URL is empty or contains control characters")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API base URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("API base URL must use http or https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("API base URL must be an origin without user information")
    if (
        (require_origin and parsed.path not in {"", "/"})
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "API base URL must not contain a query or fragment, and X API "
            "bases must be origin-only"
        )
    if not require_origin:
        recipient = provider or ("xai" if parsed.hostname == "api.x.ai" else "openai")
        validate_provider_endpoint(value, provider=recipient, test_mode=test_mode)
        return value.rstrip("/")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def endpoint_host(
    url: str,
    *,
    urlsplit: Callable,
) -> str:
    """Return the normalised host from an API endpoint URL."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def endpoint_is_loopback(
    url: str,
    *,
    endpoint_host: Callable[[str], str],
    ipaddress: ModuleType,
) -> bool:
    """Return whether one configured endpoint is an explicit loopback host."""

    host = endpoint_host(url)
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        ".localhost"
    ):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def x_request_base_url(
    method: str,
    path: str,
    *,
    X_BASE: str,
    X_UPLOAD_BASE: str,
) -> str:
    """Select the configured origin for one literal X request.

    Only the exact authorised v2 media-upload write uses the optional upload
    origin.  Reads, tweet creation and every non-literal spelling stay on the
    primary X API origin.
    """

    if str(method) == "POST" and str(path) == "/2/media/upload":
        return X_UPLOAD_BASE
    return X_BASE


def normalised_prepared_x_request_path(
    method: str,
    path: str,
    *,
    AmbiguousRemotePostOutcome: type[Exception],
    posixpath: ModuleType,
    re: ModuleType,
    requests: ModuleType,
    unquote: Callable[[str], str],
    urlsplit: Callable,
    x_request_base_url: Callable[[str, str], str],
) -> str:
    """Return the conservative path which Requests will place on the wire.

    ``requests`` normalises dot segments and some percent-encoded characters
    while preparing a request.  Security decisions made against the caller's
    unprepared string can therefore misclassify a tweet-create target.  Decode
    repeatedly as a conservative allowance for an upstream HTTP router doing
    another decoding pass, normalise separators/dot segments, and collapse
    repeated slashes before comparing protected endpoints.
    """

    try:
        prepared = requests.Request(
            method=str(method).upper(),
            url=f"{x_request_base_url(method, path)}{path}",
        ).prepare()
    except requests.RequestException as exc:
        raise AmbiguousRemotePostOutcome(
            "X request target could not be prepared safely",
            service="x",
            request_method=method,
            request_path=path,
        ) from exc
    prepared_url = prepared.url
    if not isinstance(prepared_url, str) or not prepared_url:
        raise AmbiguousRemotePostOutcome(
            "X request target preparation returned no usable URL",
            service="x",
            request_method=method,
            request_path=path,
        )
    normalised = urlsplit(prepared_url).path
    for _pass in range(4):
        decoded = unquote(normalised)
        if decoded == normalised:
            break
        normalised = decoded
    normalised = normalised.replace("\\", "/")
    normalised = posixpath.normpath(normalised)
    normalised = re.sub(r"/+", "/", normalised)
    if not normalised.startswith("/"):
        normalised = f"/{normalised}"
    return normalised


def x_request_targets_tweet_create(
    method: str,
    path: str,
    *,
    prepared_x_create_route: Callable[[str, str], str | None],
) -> bool:
    """Return whether one prepared X request targets the tweet-create route."""

    return prepared_x_create_route(method, path) == "tweet"


def x_request_targets_media_upload(
    method: str,
    path: str,
    *,
    prepared_x_create_route: Callable[[str, str], str | None],
) -> bool:
    """Return whether one prepared X request targets the v2 media-create route."""

    return prepared_x_create_route(method, path) == "media"


def prepared_x_create_route(
    method: str,
    path: str,
    *,
    normalised_prepared_x_request_path: Callable[[str, str], str],
) -> str | None:
    """Classify the create route produced by Requests preparation."""

    if str(method).upper() != "POST":
        return None
    prepared_path = normalised_prepared_x_request_path(method, path).rstrip("/")
    if prepared_path == "/2/tweets":
        return "tweet"
    if prepared_path == "/2/media/upload":
        return "media"
    return None


def exact_x_create_route(method: str, path: str) -> str | None:
    """Return the exact authorised create route, without URL normalisation.

    URL decoding and path normalisation are useful for recognising a route that
    must be rejected, but they are not authority: an authorised create must use
    one literal method/path pair with no query, fragment, alternate spelling or
    legacy endpoint.
    """

    if str(method) != "POST":
        return None
    if str(path) == "/2/tweets":
        return "tweet"
    if str(path) == "/2/media/upload":
        return "media"
    return None


def frozen_strict_json_object(
    value: object,
    *,
    label: str,
    AmbiguousRemotePostOutcome: type[Exception],
    json: ModuleType,
) -> dict:
    """Return an isolated strict-JSON copy suitable for request transport."""

    if not isinstance(value, dict):
        raise AmbiguousRemotePostOutcome(
            f"{label} must be one JSON object",
            service="x",
        )
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AmbiguousRemotePostOutcome(
            f"{label} is not strict JSON",
            service="x",
        ) from exc
    if not isinstance(decoded, dict):
        raise AmbiguousRemotePostOutcome(
            f"{label} must remain one JSON object",
            service="x",
        )
    return decoded
