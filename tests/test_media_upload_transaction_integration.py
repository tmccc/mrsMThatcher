"""Literal-process guardrails for media-upload to main-post handoff.

The driver in this file imports the candidate in a fresh interpreter for every
phase.  All HTTP boundaries are local sentinels: no test permits a network
request.  A second interpreter inspects the durable state left by each hard
exit or injected local failure.

These are focused candidate regressions, not authoritative release-assurance
evidence.  In particular, their assertions execute in the candidate's pytest
interpreter and must not be treated as an external trust root.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).resolve()
OUTPUT_PREFIX = "MEDIA_UPLOAD_INTEGRATION_JSON="
LANES = ("quote_image", "daily_meme")
HARD_EXIT_CODES = {
    "at_transport": 71,
    "before_confirm": 72,
    "after_confirm": 73,
    "peer_delete_transport": 74,
    "peer_replace_transport": 75,
}


class _LocalTransportReached(BaseException):
    """A local sentinel reached a boundary that must remain offline."""


class _FakeResponse:
    """Minimal successful Requests response for one local media upload."""

    status_code = 201
    text = '{"data":{"id":"780001"}}'
    headers: dict[str, str] = {}

    @staticmethod
    def json() -> dict[str, dict[str, str]]:
        return {"data": {"id": "780001"}}


def _emit(value: object) -> None:
    os.write(
        1,
        OUTPUT_PREFIX.encode("ascii")
        + json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n",
    )


def _environment(state_directory: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(state_directory),
            "MRS_LOG_FILE": str(state_directory / "media-upload-integration.log"),
            "PYTHONPATH": str(ROOT),
            "X_API_BASE_URL": "http://127.0.0.1:9",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
            "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
            "X_BEARER_TOKEN": "dummy",
        }
    )
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONSTARTUP", None)
    return environment


def _run_driver(
    phase: str,
    *,
    lane: str,
    state_directory: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(DRIVER),
            "--media-upload-driver",
            phase,
            "--lane",
            lane,
            "--root",
            str(ROOT),
            "--state-directory",
            str(state_directory),
        ),
        cwd=ROOT,
        env=_environment(state_directory),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _result(process: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    records = [
        line[len(OUTPUT_PREFIX) :]
        for line in process.stdout.splitlines()
        if line.startswith(OUTPUT_PREFIX)
    ]
    if len(records) != 1:
        raise AssertionError(
            "literal media driver did not emit exactly one record\n"
            f"returncode={process.returncode}\nstdout={process.stdout}\n"
            f"stderr={process.stderr}"
        )
    value = json.loads(records[0])
    assert isinstance(value, dict)
    return value


def _import_candidate(root: Path, state_directory: Path):
    root = root.resolve(strict=True)
    state_directory = state_directory.resolve(strict=True)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import mrsMThatcher2 as bot

    if Path(bot.__file__).resolve(strict=True) != root / "mrsMThatcher2.py":
        raise RuntimeError("media integration driver imported the wrong candidate")
    if Path(bot.BASE_DIR).resolve(strict=True) != state_directory:
        raise RuntimeError("media integration driver imported the wrong state root")
    return bot


def _activate(bot: Any) -> None:
    from tests.helpers.protocol_activation import create_test_protocol_activation

    create_test_protocol_activation(
        Path(bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE)
    )
    bot._PRODUCTION_BOOTSTRAPPED = True
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False


def _image_path(state_directory: Path, lane: str) -> Path:
    return state_directory / (
        "001_meme.png" if lane == "daily_meme" else "001_quote.jpg"
    )


def _write_image(state_directory: Path, lane: str) -> Path:
    path = _image_path(state_directory, lane)
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        data = b"offline-synthetic-image\x00\x01\x02"
        if os.write(descriptor, data) != len(data):
            raise OSError("short synthetic image write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path


def _main_attempt(bot: Any, lane: str, image: Path) -> dict[str, Any]:
    if lane == "quote_image":
        text = "Offline media transaction integration quotation."
        quote_hash = bot.quote_text_hash(text)
        return bot.build_main_post_attempt(
            lane=lane,
            text=text,
            media_ids=["780001"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": image.name,
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": 3600,
                "meme_delay_seconds": None,
                "quote_history_after": [quote_hash],
                "image_history_after": [image.name],
            },
            attempt_epoch=1_800_000_000,
        )
    return bot.build_main_post_attempt(
        lane=lane,
        text=bot.MEME_POST_TEXT,
        media_ids=["780001"],
        made_with_ai=False,
        selected_identity={"meme_basename": image.name},
        recovery_plan={"next_schedule_mode": "fallback"},
        attempt_epoch=1_800_000_000,
    )


def _main_status(bot: Any, lane: str) -> tuple[str, dict[str, Any] | None]:
    if lane == "quote_image":
        return bot.load_regular_post_receipt()
    return bot.load_meme_post_receipt()


def _install_successful_media_transport(bot: Any, calls: list[str]) -> None:
    def local_request(method: str, url: str, **_kwargs: object) -> _FakeResponse:
        calls.append(f"{method.upper()} {url}")
        if method.upper() != "POST" or not url.endswith("/2/media/upload"):
            raise _LocalTransportReached("unexpected transport endpoint")
        return _FakeResponse()

    bot.requests.request = local_request
    bot.requests.post = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        _LocalTransportReached("requests.post must never be used")
    )


def _state_record(bot: Any, lane: str) -> dict[str, Any]:
    from remote_media_upload_receipt import (
        fence_path_for_receipt,
        inspect_media_upload_receipt,
    )

    media_snapshot = inspect_media_upload_receipt(bot.MEDIA_UPLOAD_RECEIPT_FILE)
    main_status, main_receipt = _main_status(bot, lane)
    main_path = (
        Path(bot.REGULAR_POST_RECEIPT_FILE)
        if lane == "quote_image"
        else Path(bot.MEME_POST_RECEIPT_FILE)
    )
    return {
        "blocking": bool(bot.ambiguous_remote_post_is_blocking()),
        "lane": lane,
        "main_receipt_present": main_path.exists(),
        "main_status": main_status,
        "main_lifecycle": (
            str(main_receipt.get("lifecycle_state"))
            if isinstance(main_receipt, dict)
            else None
        ),
        "media_present": media_snapshot is not None,
        "media_fence_present": fence_path_for_receipt(
            Path(bot.MEDIA_UPLOAD_RECEIPT_FILE)
        ).exists(),
        "media_lifecycle": (
            str(media_snapshot.document["lifecycle_state"])
            if media_snapshot is not None
            else None
        ),
    }


def _initial_phase(bot: Any, lane: str, phase: str) -> int:
    state_directory = Path(bot.BASE_DIR)
    image = _write_image(state_directory, lane)
    transport_calls: list[str] = []

    if phase in {
        "at_transport",
        "peer_delete_transport",
        "peer_replace_transport",
    }:
        def hard_exit_transport(method: str, url: str, **_kwargs: object) -> object:
            if method.upper() != "POST" or not url.endswith("/2/media/upload"):
                raise _LocalTransportReached("unexpected transport endpoint")
            if phase.startswith("peer_"):
                receipt = Path(bot.MEDIA_UPLOAD_RECEIPT_FILE)
                before = receipt.read_bytes()
                peer_pid = os.fork()
                if peer_pid == 0:
                    try:
                        if phase == "peer_delete_transport":
                            receipt.unlink()
                        else:
                            replacement = receipt.with_name("peer-replacement-media-receipt")
                            descriptor = os.open(
                                replacement,
                                os.O_CREAT
                                | os.O_EXCL
                                | os.O_WRONLY
                                | getattr(os, "O_CLOEXEC", 0),
                                0o600,
                            )
                            try:
                                if os.write(descriptor, before) != len(before):
                                    raise OSError("short replacement receipt write")
                                os.fsync(descriptor)
                            finally:
                                os.close(descriptor)
                            os.replace(replacement, receipt)
                        directory_fd = os.open(
                            receipt.parent,
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                        )
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                        os._exit(0)
                    except BaseException:
                        os._exit(97)
                waited_pid, status = os.waitpid(peer_pid, 0)
                if (
                    waited_pid != peer_pid
                    or not os.WIFEXITED(status)
                    or os.WEXITSTATUS(status) != 0
                ):
                    raise RuntimeError("literal media-receipt peer failed")
            os._exit(HARD_EXIT_CODES[phase])

        bot.requests.request = hard_exit_transport
        bot.upload_media(str(image), lane=lane)
        raise AssertionError("media transport hard-exit sentinel was not reached")

    _install_successful_media_transport(bot, transport_calls)

    if phase == "before_confirm":
        def hard_exit_confirm(*_args: object, **_kwargs: object) -> object:
            os._exit(HARD_EXIT_CODES[phase])

        bot.confirm_media_upload = hard_exit_confirm
        bot.upload_media(str(image), lane=lane)
        raise AssertionError("pre-confirmation hard-exit sentinel was not reached")

    media_id = bot.upload_media(str(image), lane=lane)
    if media_id != "780001" or transport_calls != [
        "POST http://127.0.0.1:9/2/media/upload"
    ]:
        raise AssertionError("local successful upload did not run exactly once")

    if phase == "after_confirm":
        os._exit(HARD_EXIT_CODES[phase])

    attempt = _main_attempt(bot, lane, image)
    if phase == "write_failure":
        real_atomic_write_json = bot.atomic_write_json

        def fail_main_write(path: Path, value: object, *, durable: bool = False) -> None:
            if Path(path) == Path(bot.main_post_attempt_path(attempt)):
                raise OSError("synthetic main-attempt write failure")
            real_atomic_write_json(path, value, durable=durable)

        bot.atomic_write_json = fail_main_write
        try:
            bot.write_main_post_attempt(attempt)
        except OSError as exc:
            if "synthetic main-attempt" not in str(exc):
                raise
        else:
            raise AssertionError("main-attempt write failure did not run")
        _emit({**_state_record(bot, lane), "phase": phase, "transport_calls": 1})
        return 0

    bot.write_main_post_attempt(attempt)
    if phase == "handoff_failure":
        changed = {**attempt, "media_ids": ["different-media-id"]}
        try:
            bot.handoff_confirmed_media_upload_to_main_attempt(changed)
        except bot.MediaUploadReceiptError:
            pass
        else:
            raise AssertionError("mismatched handoff unexpectedly succeeded")
        _emit({**_state_record(bot, lane), "phase": phase, "transport_calls": 1})
        return 0

    bot.handoff_confirmed_media_upload_to_main_attempt(attempt)

    if phase == "pause_after_handoff":
        bot.atomic_write_json(
            bot.CONTROL_FILE,
            {"disable_all": True, "generation": 2},
            durable=True,
        )
        bot._CONTROL_CACHE = {
            "signature": None,
            "data": {},
            "has_valid": False,
            "failure_signature": None,
        }
        try:
            bot.create_post(
                text=str(attempt["text"]),
                media_ids=[str(value) for value in attempt["media_ids"]],
                made_with_ai=bool(attempt["made_with_ai"]),
                prepared_main_post_attempt=attempt,
            )
        except bot.RemoteOperationsPaused:
            pass
        else:
            raise AssertionError("post create was not stopped by the pause")
        try:
            bot.upload_media(str(image), lane=lane)
        except (bot.RemoteOperationsPaused, bot.AmbiguousRemotePostOutcome):
            pass
        else:
            raise AssertionError("paused unresolved lane permitted another upload")

    _emit({**_state_record(bot, lane), "phase": phase, "transport_calls": 1})
    return 0


def _inspect_phase(bot: Any, lane: str) -> int:
    transport_calls: list[str] = []

    def local_transport(*_args: object, **_kwargs: object) -> object:
        transport_calls.append("transport")
        raise _LocalTransportReached

    bot.requests.request = local_transport
    bot.requests.post = local_transport
    blocked_exception = None
    try:
        bot.block_if_ambiguous_remote_post()
    except BaseException as exc:
        blocked_exception = type(exc).__name__
    image = _image_path(Path(bot.BASE_DIR), lane)
    try:
        bot.upload_media(str(image), lane=lane)
    except BaseException as exc:
        upload_result = type(exc).__name__
    else:
        upload_result = "returned"
    _emit(
        {
            **_state_record(bot, lane),
            "block_exception": blocked_exception,
            "phase": "inspect",
            "transport_calls": transport_calls,
            "upload_result": upload_result,
        }
    )
    return 0


def _legacy_phase(bot: Any, lane: str) -> int:
    transport_calls: list[str] = []

    def local_transport(*_args: object, **_kwargs: object) -> object:
        transport_calls.append("transport")
        raise _LocalTransportReached

    bot.requests.request = local_transport
    bot.requests.post = local_transport
    image = _write_image(Path(bot.BASE_DIR), lane)
    try:
        bot.upload_media_v1_1(str(image))
    except bot.AmbiguousRemotePostOutcome as exc:
        result = type(exc).__name__
    else:
        result = "returned"
    _emit(
        {
            "lane": lane,
            "phase": "legacy_v1_1",
            "result": result,
            "transport_calls": transport_calls,
        }
    )
    return 0


def _driver_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--media-upload-driver", dest="phase", required=True)
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    bot = _import_candidate(arguments.root, arguments.state_directory)
    _activate(bot)
    if arguments.phase == "inspect":
        return _inspect_phase(bot, arguments.lane)
    if arguments.phase == "legacy_v1_1":
        return _legacy_phase(bot, arguments.lane)
    return _initial_phase(bot, arguments.lane, arguments.phase)


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize(
    "phase, expected_lifecycle",
    (
        ("at_transport", "sending"),
        ("before_confirm", "sending"),
        ("after_confirm", "confirmed"),
    ),
)
def test_hard_exit_media_boundaries_leave_fresh_process_globally_blocked(
    tmp_path: Path,
    lane: str,
    phase: str,
    expected_lifecycle: str,
) -> None:
    state_directory = tmp_path / f"{lane}-{phase}"
    state_directory.mkdir()
    first = _run_driver(phase, lane=lane, state_directory=state_directory)
    assert first.returncode == HARD_EXIT_CODES[phase], (
        first.stdout,
        first.stderr,
    )

    second = _run_driver("inspect", lane=lane, state_directory=state_directory)
    assert second.returncode == 0, (second.stdout, second.stderr)
    result = _result(second)
    assert result["media_present"] is True
    assert result["media_fence_present"] is True
    assert result["media_lifecycle"] == expected_lifecycle
    assert result["main_status"] == "absent"
    assert result["blocking"] is True
    assert result["block_exception"] == "AmbiguousRemotePostOutcome"
    assert result["upload_result"] == "AmbiguousRemotePostOutcome"
    assert result["transport_calls"] == []


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize(
    "phase",
    ("peer_delete_transport", "peer_replace_transport"),
)
def test_peer_namespace_change_after_media_authority_validation_stays_blocked(
    tmp_path: Path,
    lane: str,
    phase: str,
) -> None:
    """A literal peer mutates the receipt at the actual request boundary."""

    state_directory = tmp_path / f"{lane}-{phase}"
    state_directory.mkdir()
    first = _run_driver(phase, lane=lane, state_directory=state_directory)
    assert first.returncode == HARD_EXIT_CODES[phase], (
        first.stdout,
        first.stderr,
    )

    second = _run_driver("inspect", lane=lane, state_directory=state_directory)
    assert second.returncode == 0, (second.stdout, second.stderr)
    result = _result(second)
    assert result["blocking"] is True
    assert result["media_fence_present"] is True
    assert result["block_exception"] == "AmbiguousRemotePostOutcome"
    assert result["upload_result"] == "AmbiguousRemotePostOutcome"
    assert result["transport_calls"] == []


@pytest.mark.parametrize("lane", LANES)
def test_exact_confirmed_media_handoff_retires_media_but_preserves_main_receipt(
    tmp_path: Path,
    lane: str,
) -> None:
    state_directory = tmp_path / f"{lane}-handoff"
    state_directory.mkdir()
    handoff = _run_driver("handoff", lane=lane, state_directory=state_directory)
    assert handoff.returncode == 0, (handoff.stdout, handoff.stderr)
    handoff_result = _result(handoff)
    assert handoff_result["transport_calls"] == 1
    assert handoff_result["media_present"] is False
    assert handoff_result["media_fence_present"] is False
    assert handoff_result["main_status"] == "sending"
    assert handoff_result["main_lifecycle"] == "sending"

    inspect = _run_driver("inspect", lane=lane, state_directory=state_directory)
    assert inspect.returncode == 0, (inspect.stdout, inspect.stderr)
    inspected = _result(inspect)
    assert inspected["blocking"] is True
    assert inspected["media_present"] is False
    assert inspected["media_fence_present"] is False
    assert inspected["main_status"] == "sending"
    assert inspected["transport_calls"] == []


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("phase", ("write_failure", "handoff_failure"))
def test_main_attempt_write_or_handoff_failure_leaves_restart_barrier(
    tmp_path: Path,
    lane: str,
    phase: str,
) -> None:
    state_directory = tmp_path / f"{lane}-{phase}"
    state_directory.mkdir()
    failure = _run_driver(phase, lane=lane, state_directory=state_directory)
    assert failure.returncode == 0, (failure.stdout, failure.stderr)
    failed = _result(failure)
    assert failed["transport_calls"] == 1
    assert failed["media_present"] is True
    assert failed["media_fence_present"] is True
    assert failed["media_lifecycle"] == "confirmed"
    assert failed["main_status"] == (
        "absent" if phase == "write_failure" else "sending"
    )
    assert failed["blocking"] is True

    inspect = _run_driver("inspect", lane=lane, state_directory=state_directory)
    assert inspect.returncode == 0, (inspect.stdout, inspect.stderr)
    inspected = _result(inspect)
    assert inspected["blocking"] is True
    assert inspected["transport_calls"] == []


@pytest.mark.parametrize("lane", LANES)
def test_pause_after_handoff_keeps_main_receipt_and_never_reuploads(
    tmp_path: Path,
    lane: str,
) -> None:
    state_directory = tmp_path / f"{lane}-pause"
    state_directory.mkdir()
    paused = _run_driver(
        "pause_after_handoff",
        lane=lane,
        state_directory=state_directory,
    )
    assert paused.returncode == 0, (paused.stdout, paused.stderr)
    paused_result = _result(paused)
    assert paused_result["transport_calls"] == 1
    assert paused_result["media_present"] is False
    assert paused_result["media_fence_present"] is False
    assert paused_result["main_status"] == "sending"
    assert paused_result["main_lifecycle"] == "sending"
    assert paused_result["blocking"] is True

    inspect = _run_driver("inspect", lane=lane, state_directory=state_directory)
    assert inspect.returncode == 0, (inspect.stdout, inspect.stderr)
    inspected = _result(inspect)
    assert inspected["media_present"] is False
    assert inspected["media_fence_present"] is False
    assert inspected["main_status"] == "sending"
    assert inspected["transport_calls"] == []


@pytest.mark.parametrize("lane", LANES)
def test_legacy_v1_1_helper_refuses_before_any_transport(
    tmp_path: Path,
    lane: str,
) -> None:
    state_directory = tmp_path / f"{lane}-legacy"
    state_directory.mkdir()
    legacy = _run_driver("legacy_v1_1", lane=lane, state_directory=state_directory)
    assert legacy.returncode == 0, (legacy.stdout, legacy.stderr)
    result = _result(legacy)
    assert result == {
        "lane": lane,
        "phase": "legacy_v1_1",
        "result": "AmbiguousRemotePostOutcome",
        "transport_calls": [],
    }


if __name__ == "__main__":
    raise SystemExit(_driver_main(sys.argv[1:]))
