"""Local self-test orchestration and result logging.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def _self_test_ok(
    label: str,
    ok: bool,
    detail: str = '',
    *,
    log: Any,
) -> bool:
    status = "OK" if ok else "FAIL"
    message = f"SELFTEST {status}: {label}"
    if detail:
        message += f" - {detail}"
    if ok:
        log.info(message)
    else:
        log.error(message)
    return ok


def _self_test_warn(
    label: str,
    ok: bool,
    detail: str = '',
    *,
    log: Any,
) -> None:
    status = "OK" if ok else "WARN"
    message = f"SELFTEST {status}: {label}"
    if detail:
        message += f" - {detail}"
    if ok:
        log.info(message)
    else:
        log.warning(message)


def run_self_test(
    *,
    ACCESS_SECRET: Any,
    ACCESS_TOKEN: Any,
    BASE_DIR: Any,
    CONSUMER_KEY: Any,
    CONSUMER_SECRET: Any,
    CONTROL_FILE: Any,
    ENABLE_AUTO_REPLIES: Any,
    ENABLE_DAILY_MEME_POSTS: Any,
    EXTRA_QUOTE_WATCH_FILE: Any,
    IMAGE_GLOB: Any,
    LINES_FILE: Any,
    LOCAL_CONFIG_ALLOWED_KEYS: Any,
    LOCAL_CONFIG_FILE: Any,
    MAX_AUTO_REPLIES_PER_DAY: Any,
    MAX_QUOTE_REPLIES_PER_DAY: Any,
    MEME_ANALYSIS_FILE: Any,
    MEME_DIR: Any,
    MIN_SECONDS_BETWEEN_REPLIES: Any,
    MY_USER_ID: Any,
    OPENAI_API_KEY: Any,
    QUOTE_CHECK_EVERY_SECONDS: Any,
    REPLY_CHECK_EVERY_SECONDS: Any,
    STATE_FILE: Any,
    X_BEARER_TOKEN: Any,
    _runtime_config_namespace: Any,
    _self_test_ok: Any,
    _self_test_warn: Any,
    glob: Any,
    json: Any,
    list_meme_candidates: Any,
    load_control: Any,
    load_extra_quote_watch_post_ids: Any,
    load_validated_local_config_overrides: Any,
    log: Any,
    require_production_bootstrap: Any,
    single_call_reply: Any,
    validate_runtime_config_values: Any,
) -> int:
    """Run local checks without posting or calling X or OpenAI."""
    require_production_bootstrap()
    log.info("Running self-test only; no X or OpenAI API calls will be made")
    failures = 0

    def require(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not _self_test_ok(label, ok, detail):
            failures += 1

    require("base directory exists", BASE_DIR.exists(), str(BASE_DIR))
    require("lines file exists", LINES_FILE.exists(), str(LINES_FILE))
    if LINES_FILE.exists():
        try:
            with open(LINES_FILE, "r") as f:
                non_empty_lines = sum(1 for line in f if line.strip())
            require("lines file has non-empty lines", non_empty_lines > 0, f"non_empty_lines={non_empty_lines}")
        except Exception as exc:
            require("lines file readable", False, str(exc))

    images = glob(IMAGE_GLOB)
    require("quote/image image glob has files", len(images) > 0, f"count={len(images)} glob={IMAGE_GLOB}")

    try:
        overrides = load_validated_local_config_overrides()
        _self_test_warn(
            "local config file present",
            overrides is not None,
            str(LOCAL_CONFIG_FILE),
        )
        if overrides is not None:
            require(
                "local config validates",
                True,
                f"overrides={len(overrides)}",
            )
    except Exception as exc:
        _self_test_warn("local config file present", True, str(LOCAL_CONFIG_FILE))
        require("local config validates", False, str(exc))

    _self_test_warn("runtime control file absent", not CONTROL_FILE.exists(), str(CONTROL_FILE))
    ctrl = load_control()
    require(
        "runtime control validates",
        not bool(ctrl.get("_control_fail_closed", False)),
        str(CONTROL_FILE),
    )

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
            require("state file parses", isinstance(state, dict), str(STATE_FILE))
            _self_test_warn("state has next_reply_lane_priority", "next_reply_lane_priority" in state)
            _self_test_warn("state has hot-post since_id map", isinstance(state.get("hot_post_reply_since_ids", {}), dict))
        except Exception as exc:
            require("state file parses", False, str(exc))
    else:
        _self_test_warn("state file present", False, str(STATE_FILE))

    if ENABLE_DAILY_MEME_POSTS:
        _self_test_warn("meme directory present", MEME_DIR.exists(), str(MEME_DIR))
        if MEME_DIR.exists():
            meme_count = len(list_meme_candidates())
            _self_test_warn("meme candidates available", meme_count > 0, f"count={meme_count}")
        _self_test_warn("meme analysis file present", MEME_ANALYSIS_FILE.exists(), str(MEME_ANALYSIS_FILE))

    _self_test_warn("extra quote watch file present", EXTRA_QUOTE_WATCH_FILE.exists(), str(EXTRA_QUOTE_WATCH_FILE))
    if EXTRA_QUOTE_WATCH_FILE.exists():
        try:
            watch_ids = load_extra_quote_watch_post_ids()
            _self_test_warn("extra quote watch IDs loaded", True, f"count={len(watch_ids)}")
        except Exception as exc:
            require("extra quote watch file readable", False, str(exc))

    require("X_CONSUMER_KEY set", bool(CONSUMER_KEY))
    require("X_CONSUMER_SECRET set", bool(CONSUMER_SECRET))
    require("X_ACCESS_TOKEN set", bool(ACCESS_TOKEN))
    require("X_ACCESS_SECRET set", bool(ACCESS_SECRET))
    require("X_MY_USER_ID set", bool(MY_USER_ID))
    if ENABLE_AUTO_REPLIES and single_call_reply.get("enabled") is True:
        require(
            "OPENAI_API_KEY set when single-call replies enabled",
            bool(OPENAI_API_KEY),
        )
    _self_test_warn("X_BEARER_TOKEN set", bool(X_BEARER_TOKEN), "needed/preferred for quote/hot search")

    require("MAX_AUTO_REPLIES_PER_DAY positive", int(MAX_AUTO_REPLIES_PER_DAY) > 0, str(MAX_AUTO_REPLIES_PER_DAY))
    require("MAX_QUOTE_REPLIES_PER_DAY positive", int(MAX_QUOTE_REPLIES_PER_DAY) > 0, str(MAX_QUOTE_REPLIES_PER_DAY))
    require("MIN_SECONDS_BETWEEN_REPLIES positive", int(MIN_SECONDS_BETWEEN_REPLIES) > 0, str(MIN_SECONDS_BETWEEN_REPLIES))
    require("REPLY_CHECK_EVERY_SECONDS positive", int(REPLY_CHECK_EVERY_SECONDS) > 0, str(REPLY_CHECK_EVERY_SECONDS))
    require("QUOTE_CHECK_EVERY_SECONDS positive", int(QUOTE_CHECK_EVERY_SECONDS) > 0, str(QUOTE_CHECK_EVERY_SECONDS))
    runtime_config_errors = validate_runtime_config_values(
        {name: _runtime_config_namespace()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in _runtime_config_namespace()}
    )
    require("runtime config validates", not runtime_config_errors, "; ".join(runtime_config_errors))

    if failures:
        log.error("Self-test finished with %d failure(s)", failures)
        return 1

    log.info("Self-test finished successfully")
    return 0
