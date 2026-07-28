#!/usr/bin/env python3
"""Accelerated offline regular quote/image selection simulator.

The simulator reads one consistent snapshot of production inputs, then runs the
real production selectors against private state. It never calls posting, upload,
reply, lock, X, or xAI functions. All mutable writes are confined to one marked
simulation session directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import logging
import math
import os
import platform
import random
import shutil
import socket
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "simulation_runs"
SESSION_MARKER = ".mrs_regular_post_simulation"
DIAGNOSTIC_IMAGE = "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png"
MUTABLE_INPUTS = ("bot_state.json", "images_used.json", "lines_used.json")
METADATA_INPUTS = (
    "mrsMThatcher.local.json",
    "mrsMThatcher.txt",
    "quote_analysis.json",
    "image_analysis.json",
    "generated_image_analysis.json",
    "original_image_editorial_analysis_experiment_v1.json",
    "generated_image_identity_dependence_audit.json",
)
PRODUCTION_RECEIPTS = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
)


class SimulationSafetyError(RuntimeError):
    """Raised when the simulator attempts a prohibited operation."""
    pass


class SelectionCaptureError(RuntimeError):
    """Raised when a simulated production selection cannot be captured."""
    pass


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_same_or_child(path: Path, parent: Path) -> bool:
    """Return whether is same or child."""
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    return resolved == parent_resolved or parent_resolved in resolved.parents


class PrivateWriter:
    """Persist and manage private records."""
    def __init__(self, session_dir: Path):
        """Initialise the private writer."""
        self.session_dir = session_dir.resolve()

    def check(self, path: Path) -> Path:
        """Reject any attempted write to a protected production path."""
        resolved = path.resolve()
        if not is_same_or_child(resolved, self.session_dir):
            raise SimulationSafetyError(f"simulation write escaped private session: {resolved}")
        return resolved

    def mkdir(self, path: Path) -> Path:
        """Return the mkdir."""
        checked = self.check(path)
        checked.mkdir(parents=True, exist_ok=True)
        return checked

    def atomic_json(self, path: Path, value: object) -> None:
        """Perform the atomic JSON operation."""
        checked = self.check(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        temp = checked.with_name(checked.name + ".tmp")
        self.check(temp)
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, checked)

    def write_text(self, path: Path, text: str) -> None:
        """Write text."""
        checked = self.check(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        checked.write_text(text, encoding="utf-8")

    def atomic_text(self, path: Path, text: str) -> None:
        """Perform the atomic text operation."""
        checked = self.check(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        temp = checked.with_name(checked.name + ".tmp")
        self.check(temp)
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, checked)

    def append_jsonl(self, path: Path, value: object) -> None:
        """Append jsonl."""
        checked = self.check(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        with checked.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


def stat_identity(path: Path) -> tuple[int, int, int, int]:
    """Return the stat identity."""
    stat = path.stat()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def read_stable_group_once(paths: Iterable[Path]) -> dict[Path, bytes] | None:
    """Read stable group once."""
    paths = list(paths)
    before = {path: stat_identity(path) for path in paths}
    payload = {path: path.read_bytes() for path in paths}
    after = {path: stat_identity(path) for path in paths}
    if before == after and all(len(payload[path]) == before[path][1] for path in paths):
        return payload
    return None


def read_consistent_group(
    paths: Iterable[Path],
    retries: int = 8,
    quiescence_seconds: float = 0.1,
) -> dict[Path, bytes]:
    """Read consistent group."""
    paths = list(paths)
    for attempt in range(1, retries + 1):
        first = read_stable_group_once(paths)
        if first is not None:
            if quiescence_seconds > 0:
                time.sleep(quiescence_seconds)
            second = read_stable_group_once(paths)
            if second == first:
                return second
        if attempt < retries:
            time.sleep(0.05)
    raise RuntimeError("could not obtain two matching stable production state snapshots after retries")


def copy_stable_file(source: Path, destination: Path, retries: int = 5) -> str:
    """Copy stable file."""
    for attempt in range(1, retries + 1):
        before = stat_identity(source)
        data = source.read_bytes()
        after = stat_identity(source)
        if before == after and len(data) == before[1]:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            shutil.copystat(source, destination, follow_symlinks=True)
            return sha256_bytes(data)
        if attempt < retries:
            time.sleep(0.05)
    raise RuntimeError(f"could not snapshot stable file: {source}")


def validate_mutable_snapshot(payload: dict[Path, bytes]) -> None:
    """Validate mutable snapshot."""
    by_name = {path.name: json.loads(data) for path, data in payload.items()}
    state = by_name["bot_state.json"]
    images_used = {str(value) for value in by_name["images_used.json"]}
    last_image = str(state.get("last_regular_image_filename") or "")
    if last_image and int(state.get("last_quote_post_epoch", 0) or 0) > 0 and last_image not in images_used:
        raise RuntimeError("snapshot is inconsistent: last regular image is absent from image used-history")
    last_quote_epoch = int(state.get("last_quote_post_epoch", 0) or 0)
    next_quote_epoch = int(state.get("next_quote_post_epoch", 0) or 0)
    if last_quote_epoch and next_quote_epoch and next_quote_epoch <= last_quote_epoch:
        raise RuntimeError("snapshot is inconsistent: next quote epoch is not after the last quote epoch")


def git_output(*args: str) -> str:
    """Return the git output."""
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def parse_start_time(value: str) -> int:
    """Parse start time."""
    if value.lower() == "now":
        return int(datetime.now().timestamp())
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp())


def default_session_id() -> str:
    """Return the default session ID."""
    return datetime.now().strftime("regular_post_sim_%Y%m%d_%H%M%S")


def validate_session_path(path: Path) -> Path:
    """Validate session path."""
    resolved = path.expanduser().resolve()
    sensitive = {
        (ROOT / name).resolve()
        for name in (*MUTABLE_INPUTS, "mrsMThatcher.local.json", "mrsMThatcher.log", "mrsMThatcher.lock")
    }
    if resolved in sensitive or resolved == ROOT.resolve():
        raise SimulationSafetyError(f"unsafe simulation output path: {resolved}")
    if is_same_or_child(resolved, ROOT) and not is_same_or_child(resolved, DEFAULT_OUTPUT_ROOT):
        raise SimulationSafetyError(
            f"output inside project must be under {DEFAULT_OUTPUT_ROOT.resolve()}: {resolved}"
        )
    return resolved


def make_session_directory(path: Path, *, overwrite: bool, resume: bool) -> tuple[Path, PrivateWriter]:
    """Create session directory."""
    path = validate_session_path(path)
    if resume:
        if not (path / SESSION_MARKER).is_file():
            raise SimulationSafetyError(f"resume target is not a marked simulation session: {path}")
    elif path.exists():
        if not overwrite:
            raise FileExistsError(f"simulation session already exists: {path}")
        if not (path / SESSION_MARKER).is_file():
            raise SimulationSafetyError(f"refusing to overwrite unmarked directory: {path}")
        for existing in path.rglob("*"):
            try:
                existing.chmod(0o700 if existing.is_dir() else 0o600)
            except FileNotFoundError:
                pass
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    writer = PrivateWriter(path)
    writer.write_text(path / SESSION_MARKER, "MrsMThatcher private regular-post simulation session\n")
    return path, writer


def snapshot_inputs(session_dir: Path, writer: PrivateWriter) -> tuple[Path, dict]:
    """Return the snapshot inputs."""
    snapshot = writer.mkdir(session_dir / "input_snapshot")
    hashes: dict[str, str] = {}

    mutable_paths = [ROOT / name for name in MUTABLE_INPUTS]
    receipt_paths = [ROOT / name for name in PRODUCTION_RECEIPTS]
    if any(path.exists() for path in receipt_paths):
        raise RuntimeError("cannot snapshot while a production receipt exists")
    stable_group = read_consistent_group(mutable_paths)
    if any(path.exists() for path in receipt_paths):
        raise RuntimeError("production receipt appeared during snapshot capture")
    validate_mutable_snapshot(stable_group)
    for source, data in stable_group.items():
        destination = snapshot / source.name
        writer.check(destination).write_bytes(data)
        shutil.copystat(source, destination)
        hashes[source.name] = sha256_bytes(data)

    for name in METADATA_INPUTS:
        source = ROOT / name
        if not source.is_file():
            raise FileNotFoundError(f"required simulation input missing: {source}")
        hashes[name] = copy_stable_file(source, snapshot / name)

    research_source = ROOT / "semantic_alignment_research/quote_research_full_001/research_packets.json"
    if not research_source.is_file():
        raise FileNotFoundError(f"required simulation input missing: {research_source}")
    hashes["research_packets.json"] = copy_stable_file(research_source, snapshot / "research_packets.json")

    image_specs = (
        (ROOT / "images", snapshot / "images", "t*"),
        (ROOT / "generated_review_approved_images", snapshot / "generated_images", "*.png"),
    )
    image_counts: dict[str, int] = {}
    for source_dir, destination_dir, pattern in image_specs:
        writer.mkdir(destination_dir)
        count = 0
        for source in sorted(source_dir.glob(pattern)):
            if not source.is_file():
                continue
            relative = f"{destination_dir.name}/{source.name}"
            hashes[relative] = copy_stable_file(source, destination_dir / source.name)
            count += 1
        image_counts[destination_dir.name] = count

    quote_data = json.loads((snapshot / "quote_analysis.json").read_text(encoding="utf-8"))
    quote_count = len(quote_data.get("items", {}))
    manifest = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(ROOT),
        "git_branch": git_output("branch", "--show-current"),
        "git_commit": git_output("rev-parse", "HEAD"),
        "python_version": platform.python_version(),
        "source_paths": {name: str(ROOT / name) for name in (*MUTABLE_INPUTS, *METADATA_INPUTS)},
        "sha256": dict(sorted(hashes.items())),
        "original_image_count": image_counts.get("images", 0),
        "generated_image_count": image_counts.get("generated_images", 0),
        "quote_count": quote_count,
        "snapshot_consistency": (
            "Mutable state files were read twice as stat-before/read/stat-after groups across a quiescence window; "
            "the complete logical snapshots had to match. "
            "Each immutable metadata/image file was likewise copied only across a stable stat window."
        ),
    }
    writer.atomic_json(snapshot / "manifest.json", manifest)

    for path in sorted(snapshot.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    snapshot.chmod(0o555)
    return snapshot, manifest


def copy_existing_snapshot(source: Path, session_dir: Path, writer: PrivateWriter) -> tuple[Path, dict]:
    """Copy existing snapshot."""
    source = source.expanduser().resolve()
    if not (source / "manifest.json").is_file():
        raise ValueError(f"snapshot directory has no manifest.json: {source}")
    destination = session_dir / "input_snapshot"
    writer.check(destination)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest.get("sha256", {}).items():
        copied = destination / relative
        if not copied.is_file() or sha256_file(copied) != expected:
            raise RuntimeError(f"copied snapshot hash mismatch: {relative}")
    return destination, manifest


def configure_session_logging(bot: Any, log_path: Path) -> None:
    """Configure session logging."""
    for handler in list(bot.log.handlers):
        bot.log.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(funcName)s:%(lineno)d - %(message)s"))
    handler.setLevel(logging.WARNING)
    bot.log.addHandler(handler)
    bot.log.setLevel(logging.INFO)
    bot.log.propagate = False


def import_production_bot(session_dir: Path) -> Any:
    """Return the import production bot."""
    import_base = Path("/tmp") / f"mrsMThatcher-simulator-{os.getpid()}"
    import_base.mkdir(parents=True, exist_ok=True)
    env = {
        "MRS_TEST_MODE": "1",
        "MRS_BASE_DIR": str(import_base),
        "MRS_LOG_FILE": str(import_base / "import.log"),
        "LOG_LEVEL": "CRITICAL",
        "X_API_BASE_URL": "http://127.0.0.1:9",
        "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
        "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
        "X_CONSUMER_KEY": "simulator-disabled",
        "X_CONSUMER_SECRET": "simulator-disabled",
        "X_ACCESS_TOKEN": "simulator-disabled",
        "X_ACCESS_SECRET": "simulator-disabled",
        "X_MY_USER_ID": "0",
        "XAI_API_KEY": "simulator-disabled",
        "X_BEARER_TOKEN": "simulator-disabled",
    }
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        with block_process_network():
            bot = importlib.import_module("mrsMThatcher2")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    configure_session_logging(bot, session_dir / "simulator.log")
    return bot


def apply_snapshot_config(bot: Any, snapshot: Path) -> dict:
    """Apply snapshot config."""
    config = json.loads((snapshot / "mrsMThatcher.local.json").read_text(encoding="utf-8"))
    candidate: dict[str, object] = {}
    for key, value in config.items():
        if key not in bot.LOCAL_CONFIG_ALLOWED_KEYS:
            continue
        candidate[key] = bot._coerce_local_config_value(key, value, getattr(bot, key))
    errors = bot.validate_runtime_config_values(candidate)
    if errors:
        raise RuntimeError("invalid snapshotted local config: " + "; ".join(errors))
    for key, value in candidate.items():
        setattr(bot, key, value)
    return config


def configure_snapshot_paths(bot: Any, snapshot: Path, run_dir: Path) -> None:
    """Configure snapshot paths."""
    bot.LINES_FILE = snapshot / "mrsMThatcher.txt"
    bot.QUOTE_ANALYSIS_FILE = snapshot / "quote_analysis.json"
    bot.HISTORICAL_CONTEXT_RESEARCH_DIR = snapshot
    bot.COMPLETED_QUOTE_RESEARCH_FILE = snapshot / "research_packets.json"
    bot.RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE = (
        snapshot / "runtime_eligible_quote_manifest.json"
    )
    bot.IMAGE_ANALYSIS_FILE = snapshot / "image_analysis.json"
    bot.GENERATED_IMAGE_ANALYSIS_FILE = str(snapshot / "generated_image_analysis.json")
    bot.IMAGE_GLOB = str(snapshot / "images" / "t*")
    bot.GENERATED_IMAGE_DIR = str(snapshot / "generated_images")
    bot.GENERATED_IMAGE_GLOB = "*.png"
    bot.ORIGINAL_EDITORIAL_ANALYSIS_FILE = str(snapshot / "original_image_editorial_analysis_experiment_v1.json")
    bot.GENERATED_IDENTITY_AUDIT_FILE = str(snapshot / "generated_image_identity_dependence_audit.json")
    bot.STATE_FILE = run_dir / "state.json"
    bot.IMAGES_USED_FILE = run_dir / "images_used.json"
    bot.LINES_USED_FILE = run_dir / "lines_used.json"
    bot.REGULAR_POST_RECEIPT_FILE = run_dir / "forbidden_regular_post_receipt.json"
    bot.MEME_POST_RECEIPT_FILE = run_dir / "forbidden_meme_post_receipt.json"
    bot.CONFIRMED_REPLY_RECEIPT_FILE = run_dir / "forbidden_reply_receipt.json"
    bot.LOCK_FILE = run_dir / "forbidden.lock"
    bot._ORIGINAL_EDITORIAL_ANALYSIS_CACHE = {}
    bot._GENERATED_IDENTITY_AUDIT_CACHE = {}

    original_load_quote_analysis = bot.load_quote_analysis
    original_load_image_analysis = bot.load_image_analysis
    quote_analysis_cache: list[object] = []
    image_analysis_cache: list[object] = []

    def cached_quote_analysis() -> object:
        if not quote_analysis_cache:
            quote_analysis_cache.append(original_load_quote_analysis())
        return quote_analysis_cache[0]

    def cached_image_analysis() -> object:
        if not image_analysis_cache:
            image_analysis_cache.append(original_load_image_analysis())
        return image_analysis_cache[0]

    bot.load_quote_analysis = cached_quote_analysis
    bot.load_image_analysis = cached_image_analysis

    original_sha = bot.current_image_sha256
    cache: dict[str, str] = {}

    def cached_sha(path: str) -> str:
        key = str(Path(path).resolve())
        if key not in cache:
            cache[key] = original_sha(path)
        return cache[key]

    bot.current_image_sha256 = cached_sha


def forbidden_operation(name: str):
    """Return the forbidden operation."""
    def fail(*args: object, **kwargs: object) -> Any:
        raise SimulationSafetyError(f"forbidden simulator operation attempted: {name}")

    return fail


def install_hard_guards(bot: Any, writer: PrivateWriter) -> None:
    """Install hard guards."""
    for name in (
        "upload_media",
        "create_post",
        "generate_ai_first_reply",
        "acquire_instance_lock",
        "post_random_quote",
        "post_next_meme",
        "maybe_reply_to_mentions",
        "maybe_reply_to_quote_tweets",
        "write_regular_post_receipt",
        "write_meme_post_receipt",
        "write_confirmed_reply_receipt",
        "remove_regular_post_receipt",
        "remove_meme_post_receipt",
        "remove_confirmed_reply_receipt",
    ):
        setattr(bot, name, forbidden_operation(name))

    def guarded_atomic(path: Path, value: object, *, durable: bool = False) -> None:
        writer.atomic_json(Path(path), value)

    def guarded_save_used(path: Path, value: set, *, durable: bool = False) -> None:
        writer.atomic_json(Path(path), sorted(str(item) for item in value))

    bot.atomic_write_json = guarded_atomic
    bot.save_used_set = guarded_save_used
    bot.save_quote_used_hashes = guarded_save_used
    bot.save_image_used_basenames = guarded_save_used
    bot.save_state = lambda state, **kwargs: writer.atomic_json(Path(bot.STATE_FILE), state)

    for method in ("request", "get", "post", "put", "patch", "delete"):
        setattr(bot.requests, method, forbidden_operation(f"network requests.{method}"))


@contextmanager
def block_process_network() -> Iterable[None]:
    """Block process network."""
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto

    def blocked(*args: object, **kwargs: object) -> Any:
        raise SimulationSafetyError("outbound network is disabled in core simulation")

    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked
    try:
        yield
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.socket.sendto = original_sendto


class JsonEventCapture(logging.Handler):
    """Represent JSON event capture data."""
    def __init__(self) -> None:
        """Initialise the JSON event capture."""
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Capture one structured event emitted by the isolated simulator."""
        self.messages.append(record.getMessage())

    def payload(self, prefix: str) -> dict | None:
        """Return the payload."""
        for message in reversed(self.messages):
            if message.startswith(prefix):
                return json.loads(message[len(prefix) :])
        return None


@contextmanager
def capture_shadow_selection(bot: Any) -> Iterable[dict]:
    """Yield capture shadow selection values."""
    capture: dict[str, Any] = {"scored_ids": []}
    events = JsonEventCapture()
    bot.log.addHandler(events)
    original_editorial = bot.log_original_editorial_shadow_result
    original_identity = bot.log_generated_identity_policy_shadow_result
    original_editorial_enabled = bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING
    original_identity_enabled = bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING

    def editorial_hook(quote: dict, chosen: dict, scored: list[dict], *, selection_phase: str) -> None:
        capture["quote"] = quote
        capture["chosen"] = chosen
        capture["scored"] = scored
        capture["selection_phase"] = selection_phase
        capture["scored_ids"].append(id(scored))
        bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING = True
        original_editorial(quote, chosen, scored, selection_phase=selection_phase)

    def identity_hook(
        quote: dict,
        chosen: dict,
        scored: list[dict],
        *,
        selection_phase: str,
        selection_rng_state: object | None = None,
    ) -> None:
        capture["scored_ids"].append(id(scored))
        bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING = True
        original_identity(
            quote,
            chosen,
            scored,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
        )

    bot.log_original_editorial_shadow_result = editorial_hook
    bot.log_generated_identity_policy_shadow_result = identity_hook
    try:
        yield capture
        capture["original_editorial_shadow"] = events.payload("ORIGINAL_EDITORIAL_SHADOW_RESULT ")
        capture["generated_identity_shadow"] = events.payload("GENERATED_IDENTITY_POLICY_SHADOW_RESULT ")
        if len(set(capture.get("scored_ids", []))) > 1:
            raise SelectionCaptureError("shadow observers did not receive the same scored candidate list")
    finally:
        bot.log.removeHandler(events)
        bot.log_original_editorial_shadow_result = original_editorial
        bot.log_generated_identity_policy_shadow_result = original_identity
        bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING = original_editorial_enabled
        bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING = original_identity_enabled


@contextmanager
def capture_scored_selection(bot: Any) -> Iterable[dict]:
    """Yield capture scored selection values."""
    capture: dict[str, Any] = {"scored_ids": []}
    original_editorial = bot.log_original_editorial_shadow_result
    original_identity = bot.log_generated_identity_policy_shadow_result

    def hook(
        quote: dict,
        chosen: dict,
        scored: list[dict],
        *,
        selection_phase: str,
        **_kwargs: object,
    ) -> None:
        capture["quote"] = quote
        capture["chosen"] = chosen
        capture["scored"] = scored
        capture["selection_phase"] = selection_phase
        capture["scored_ids"].append(id(scored))

    bot.log_original_editorial_shadow_result = hook
    bot.log_generated_identity_policy_shadow_result = hook
    try:
        yield capture
        scored_ids = capture.get("scored_ids", [])
        if not scored_ids or len(set(scored_ids)) != 1:
            raise SelectionCaptureError("candidate capture hooks did not receive one exact scored list")
    finally:
        bot.log_original_editorial_shadow_result = original_editorial
        bot.log_generated_identity_policy_shadow_result = original_identity


def select_with_production_recovery(
    bot: Any,
    lines_used: set[str],
    images_used: set[str],
    state: dict,
    *,
    evaluate_shadows: bool = True,
) -> dict:
    """Select with production recovery."""
    capture_context = capture_shadow_selection(bot) if evaluate_shadows else capture_scored_selection(bot)
    with capture_context as capture:
        try:
            quote, image, attempts = bot.choose_regular_quote_image_pair(lines_used, images_used, state)
        except bot.NoViableQuoteImagePair:
            try:
                quote, image, attempts = bot.choose_regular_quote_image_pair(
                    lines_used, images_used, state, force_image_cycle_reset=True
                )
            except bot.NoViableQuoteImagePair as reset_exc:
                if not reset_exc.excluded_last_image:
                    raise RuntimeError(str(reset_exc)) from reset_exc
                quote, image, attempts = bot.choose_regular_quote_image_pair(
                    lines_used,
                    images_used,
                    state,
                    force_image_cycle_reset=True,
                    avoid_last_image_at_cycle_boundary=False,
                )
    if "chosen" not in capture or capture["chosen"] is not image:
        raise SelectionCaptureError("production selection did not reach both shadow capture hooks")
    return {
        "quote": quote,
        "image": image,
        "attempts": attempts,
        "selection_phase": capture["selection_phase"],
        "scored": capture["scored"],
        "scored_object_id": capture["scored_ids"][0],
        "original_editorial_shadow": capture.get("original_editorial_shadow"),
        "generated_identity_shadow": capture.get("generated_identity_shadow"),
    }


class CounterfactualPolicyExhausted(RuntimeError):
    """Raised when a counterfactual policy has no eligible candidate."""
    pass


def derived_branch_seed(run_seed: int, branch: str) -> int:
    """Return the derived branch seed."""
    digest = hashlib.sha256(f"mrs-counterfactual-v1:{run_seed}:{branch}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def policy_candidate_rows(bot: Any, quote: dict, scored: list[dict], branch: str) -> list[dict]:
    """Return the policy candidate rows."""
    rows: list[dict] = []
    editorial = bot.load_original_editorial_analysis() if branch == "editorial" else None
    audit = bot.load_generated_identity_audit() if branch == "identity" else None
    for candidate in scored:
        row = dict(candidate)
        baseline = float(candidate["score"])
        row["baseline_score"] = baseline
        row["editorial_adjustment"] = 0.0
        row["identity_policy"] = None
        row["identity_action"] = "unchanged"
        row["identity_adjustment"] = 0.0
        row["policy_score"] = baseline
        row["policy_excluded"] = False
        row["policy_detail"] = {}
        if branch == "editorial" and candidate.get("image_source") == "original":
            adjustment, detail = bot.original_editorial_shadow_score(
                quote.get("analysis"), editorial.get(str(candidate["basename"]))
            )
            row["editorial_adjustment"] = float(adjustment)
            row["policy_score"] = baseline + float(adjustment)
            row["policy_detail"] = detail
        elif branch == "identity" and candidate.get("image_source") == "generated":
            identity = bot.generated_identity_candidate_shadow_row(candidate, audit)
            row["identity_policy"] = identity["identity_policy"]
            row["identity_action"] = identity["identity_action"]
            row["identity_adjustment"] = identity["identity_adjustment"]
            row["policy_excluded"] = identity["identity_shadow_score"] is None
            row["policy_score"] = identity["identity_shadow_score"]
        rows.append(row)
    return rows


def choose_policy_winner(bot: Any, rows: list[dict]) -> dict:
    """Select policy winner."""
    eligible = [row for row in rows if not row.get("policy_excluded")]
    if not eligible:
        raise CounterfactualPolicyExhausted("policy excluded every scored candidate")
    best = max(float(row["policy_score"]) for row in eligible)
    tied = [row for row in eligible if float(row["policy_score"]) == best]
    return bot.random.choice(tied)


def observational_policy_winner(rows: list[dict], production_basename: str) -> str | None:
    """Return the observational policy winner."""
    eligible = [row for row in rows if not row.get("policy_excluded")]
    if not eligible:
        return None
    best = max(float(row["policy_score"]) for row in eligible)
    tied = [row for row in eligible if float(row["policy_score"]) == best]
    retained = next((row for row in tied if row["basename"] == production_basename), None)
    return str((retained or min(tied, key=lambda row: str(row["basename"])))["basename"])


def score_exact_quote_phase(
    bot: Any,
    quote: dict,
    images_used: set[str],
    state: dict,
    branch: str,
    *,
    phase: str,
    cycle_boundary_exclusions: set[str] | None = None,
) -> tuple[dict, list[dict]]:
    """Score exact quote phase."""
    force_reset = phase != "normal"
    avoid_last = phase != "last_image_fallback"
    rng_before = bot.random.getstate()
    with capture_scored_selection(bot) as capture:
        bot.choose_matched_unused_image(
            images_used,
            quote,
            state,
            force_cycle_reset=force_reset,
            avoid_last_image_at_cycle_boundary=avoid_last,
            cycle_boundary_exclusions=cycle_boundary_exclusions,
            generated_images_allowed=bot.generated_images_allowed_by_spacing(state),
            selection_phase=phase,
        )
    # Production baseline tie selection is only a vehicle for obtaining the
    # exact scored list. A policy branch gets one actual tie draw of its own.
    bot.random.setstate(rng_before)
    rows = policy_candidate_rows(bot, quote, capture["scored"], branch)
    winner = choose_policy_winner(bot, rows)
    return winner, rows


def select_policy_image_with_recovery(
    bot: Any,
    quote: dict,
    images_used: set[str],
    state: dict,
    branch: str,
) -> dict:
    """Select policy image with recovery."""
    phases = ("normal", "forced_cycle_reset", "last_image_fallback")
    cycle_boundary_exclusions: set[str] = set()
    last_error: Exception | None = None
    for phase in phases:
        if phase == "last_image_fallback" and not cycle_boundary_exclusions:
            break
        try:
            winner, rows = score_exact_quote_phase(
                bot,
                quote,
                images_used,
                state,
                branch,
                phase=phase,
                cycle_boundary_exclusions=cycle_boundary_exclusions if phase != "normal" else None,
            )
            return {
                "quote": quote,
                "image": winner,
                "scored": rows,
                "selection_phase": phase,
                "policy_excluded_count": sum(bool(row.get("policy_excluded")) for row in rows),
            }
        except (bot.QuoteSpecificImageMismatch, bot.GlobalImageUnavailable, CounterfactualPolicyExhausted) as exc:
            last_error = exc
            continue
    raise CounterfactualPolicyExhausted(
        f"{branch} branch exhausted image recovery for shared quote: {last_error}"
    )


def rng_state_encode(state: object) -> str:
    """Return the RNG state encode."""
    normalised = _normalise_rng_state(state)
    return json.dumps(
        {
            "schema_version": 1,
            "state_version": normalised[0],
            "internal_state": list(normalised[1]),
            "gauss_next": normalised[2],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def rng_state_decode(value: str) -> object:
    """Return the RNG state decode."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65536:
        raise SimulationSafetyError("RNG checkpoint must be a bounded JSON string")
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise SimulationSafetyError(
            "legacy or malformed RNG checkpoint rejected; restart this seed from its initial state"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "state_version", "internal_state", "gauss_next"
    }:
        raise SimulationSafetyError("RNG checkpoint has an invalid schema")
    if payload["schema_version"] != 1:
        raise SimulationSafetyError("unsupported RNG checkpoint schema version")
    return _normalise_rng_state(
        (payload["state_version"], payload["internal_state"], payload["gauss_next"])
    )


def _normalise_rng_state(state: object) -> tuple[int, tuple[int, ...], float | None]:
    if not isinstance(state, (tuple, list)) or len(state) != 3:
        raise SimulationSafetyError("RNG state must contain exactly three fields")
    state_version, internal_state, gauss_next = state
    if type(state_version) is not int or state_version not in (2, 3):
        raise SimulationSafetyError("unsupported Python RNG state version")
    if not isinstance(internal_state, (tuple, list)) or len(internal_state) != 625:
        raise SimulationSafetyError("RNG internal state must contain exactly 625 integers")
    if any(type(item) is not int or item < -(2**63) or item >= 2**64 for item in internal_state):
        raise SimulationSafetyError("RNG internal state contains an invalid integer")
    if gauss_next is not None:
        if isinstance(gauss_next, bool) or not isinstance(gauss_next, (int, float)):
            raise SimulationSafetyError("RNG Gaussian cache must be numeric or null")
        gauss_next = float(gauss_next)
        if not math.isfinite(gauss_next):
            raise SimulationSafetyError("RNG Gaussian cache must be finite")
    normalised = (state_version, tuple(internal_state), gauss_next)
    try:
        random.Random().setstate(normalised)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SimulationSafetyError("RNG checkpoint is not accepted by Python's random module") from exc
    return normalised


def load_private_state(snapshot: Path) -> tuple[dict, set[str], set[str]]:
    """Load private state."""
    state = json.loads((snapshot / "bot_state.json").read_text(encoding="utf-8"))
    images_used = {str(item) for item in json.loads((snapshot / "images_used.json").read_text(encoding="utf-8"))}
    lines_used = {str(item) for item in json.loads((snapshot / "lines_used.json").read_text(encoding="utf-8"))}
    return state, images_used, lines_used


def persist_private_state(writer: PrivateWriter, run_dir: Path, state: dict, images_used: set[str], lines_used: set[str]) -> None:
    """Persist private state."""
    writer.atomic_json(run_dir / "state.json", state)
    writer.atomic_json(run_dir / "images_used.json", sorted(images_used))
    writer.atomic_json(run_dir / "lines_used.json", sorted(lines_used))


def checkpoint_payload(
    run_id: str,
    seed: int,
    completed_posts: int,
    last_post_epoch: int | None,
    virtual_epoch: int,
    rng_state: object,
    state: dict,
    images_used: set[str],
    lines_used: set[str],
) -> dict:
    """Return the checkpoint payload."""
    return {
        "schema_version": 1,
        "run_id": run_id,
        "seed": seed,
        "completed_posts": completed_posts,
        "last_post_epoch": last_post_epoch,
        "virtual_epoch": virtual_epoch,
        "rng_state": rng_state_encode(rng_state),
        "state": state,
        "images_used": sorted(images_used),
        "lines_used": sorted(lines_used),
    }


def reconcile_jsonl_to_checkpoint(writer: PrivateWriter, output_path: Path, completed_posts: int) -> list[dict]:
    """Reconcile jsonl to checkpoint."""
    if not output_path.exists():
        if completed_posts:
            raise RuntimeError("checkpoint records completed posts but selections.jsonl is missing")
        return []
    lines = [line for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < completed_posts:
        raise RuntimeError("selections.jsonl is behind the authoritative checkpoint")
    records = [json.loads(line) for line in lines[:completed_posts]]
    expected = list(range(1, completed_posts + 1))
    actual = [int(record.get("post_index", -1)) for record in records]
    if actual != expected:
        raise RuntimeError("selections.jsonl post indices do not match the checkpoint")
    if len(lines) > completed_posts:
        text = "".join(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n" for record in records)
        writer.atomic_text(output_path, text)
    return records


def candidate_detail_rows(bot: Any, selection: dict, mode: str) -> list[dict]:
    """Return the candidate detail rows."""
    if mode == "none":
        return []
    scored = selection["scored"]
    ordered = sorted(scored, key=lambda item: (-float(item["score"]), str(item["basename"])))
    if mode == "top10":
        ordered = ordered[:10]
    editorial = bot.load_original_editorial_analysis()
    audit = bot.load_generated_identity_audit()
    result: list[dict] = []
    for candidate in ordered:
        row = {
            "basename": candidate["basename"],
            "source": candidate["image_source"],
            "production_score": round(float(candidate["score"]), 6),
            "origin_quote_match": bool(candidate.get("origin_quote_match")),
            "components": candidate.get("components", {}),
        }
        if candidate["image_source"] == "original":
            adjustment, _detail = bot.original_editorial_shadow_score(
                selection["quote"].get("analysis"), editorial.get(candidate["basename"])
            )
            row["original_editorial_adjustment"] = round(float(adjustment), 6)
            row["original_editorial_shadow_score"] = round(float(candidate["score"]) + adjustment, 6)
        else:
            identity = bot.generated_identity_candidate_shadow_row(candidate, audit)
            row["identity_policy"] = identity["identity_policy"]
            row["identity_action"] = identity["identity_action"]
            row["identity_adjustment"] = identity["identity_adjustment"]
            row["identity_shadow_score"] = identity["identity_shadow_score"]
        result.append(row)
    return result


def apply_simulated_success(
    bot: Any,
    selection: dict,
    state: dict,
    lines_used: set[str],
    images_used: set[str],
    virtual_epoch: int,
    run_id: str,
    post_index: int,
    *,
    quote_delay: int | None = None,
    meme_delay: int | None = None,
) -> int:
    """Apply simulated success."""
    quote = selection["quote"]
    image = selection["image"]

    # Match post_random_quote RNG ordering: schedule delays are sampled only
    # after production has selected the quote/image pair.
    if quote_delay is None:
        quote_delay = bot.random.randint(bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX)
    if bot.ENABLE_DAILY_MEME_POSTS and meme_delay is None:
        meme_delay = bot.random.randint(
            bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        )

    quote_hash = str(quote["quote_hash"])
    basename = str(image["basename"])
    synthetic_id = f"sim-{run_id}-{post_index:06d}"
    lines_used.add(quote_hash)
    images_used.add(basename)
    state["last_main_post_id"] = synthetic_id
    state["last_quote_post_epoch"] = virtual_epoch
    state["last_regular_image_filename"] = basename
    bot.update_regular_generated_image_spacing_state(state, basename)
    quote_fields, _ = bot.next_quote_schedule_fields(virtual_epoch, delay=quote_delay)
    bot.apply_state_fields(state, quote_fields)
    if bot.ENABLE_DAILY_MEME_POSTS:
        meme_fields = bot.meme_schedule_fields_after_quote_post(state, virtual_epoch, delay=meme_delay)
        bot.apply_state_fields(state, meme_fields)
    return int(state["next_quote_post_epoch"])


def trace_image_lifecycle(
    bot: Any,
    basename: str,
    selection: dict,
    images_used_before: set[str],
    state_before: dict,
    image_cycle_reset: bool,
    spacing_allowed_before: bool,
) -> dict:
    """Return the trace image lifecycle."""
    quote = selection["quote"]
    scored_by_name = {item["basename"]: item for item in selection["scored"]}
    paths = {Path(path).name: path for path in bot.current_image_paths()}
    origin_hash = bot.generated_image_origin_quote_hash(basename)
    result = {
        "basename": basename,
        "discovered": basename in paths,
        "generated_spacing_allowed": bool(spacing_allowed_before),
        "origin_quote_match": bool(origin_hash and origin_hash == str(quote.get("quote_hash") or "").lower()),
        "reached_scored_candidates": basename in scored_by_name,
        "production_winner": selection["image"].get("basename") == basename,
        "reason": "unknown",
    }
    if not result["discovered"]:
        result["reason"] = "not_discovered"
        return result
    try:
        _image_hash, analysis = bot.image_metadata_for_basename(
            bot.load_image_analysis(), basename, paths[basename]
        )
    except bot.StaleImageMetadata:
        result["reason"] = "stale_metadata"
        return result
    if bot.image_is_out_of_season(analysis, bot.current_datetime().strftime("%m-%d")):
        result["reason"] = "seasonally_excluded"
        return result
    if origin_hash and not spacing_allowed_before:
        result["reason"] = "generated_spacing_blocked"
        return result
    if basename in scored_by_name:
        result["reason"] = "scored"
        return result

    phase = selection["selection_phase"]
    last_name = str(state_before.get("last_regular_image_filename") or "")
    if image_cycle_reset and basename == last_name and phase != "last_image_fallback":
        result["reason"] = "last_image_boundary"
        return result
    if basename in images_used_before and not image_cycle_reset:
        result["reason"] = "used_current_cycle"
        return result

    idf = bot.build_image_topic_idf(bot.load_image_analysis())
    _score, _components, eligible = bot.score_image_for_quote(quote.get("analysis"), analysis, idf)
    if not eligible:
        result["reason"] = "strong_visual_mismatch"
        return result
    result["reason"] = "unexplained_absence"
    return result


def make_selection_record(
    bot: Any,
    session_id: str,
    run_id: str,
    seed: int,
    post_index: int,
    virtual_epoch: int,
    interval: int,
    selection: dict,
    spacing_before: int,
    spacing_allowed_before: bool,
    spacing_after: int,
    quote_cycle_reset: bool,
    image_cycle_reset: bool,
    candidate_detail: str,
    image_traces: list[dict],
) -> dict:
    """Create selection record."""
    quote = selection["quote"]
    image = selection["image"]
    scored = selection["scored"]
    season_status = quote.get("season_status") or {}
    seasonal_multiplier = 1.0
    if season_status.get("in_window"):
        seasonal_multiplier = {
            "soft": bot.QUOTE_SEASON_SOFT_WEIGHT,
            "strong": bot.QUOTE_SEASON_STRONG_WEIGHT,
            "date_specific": bot.QUOTE_SEASON_DATE_SPECIFIC_WEIGHT,
        }.get(season_status.get("relevance"), 1.0)
    original_candidates = [item for item in scored if item.get("image_source") == "original"]
    baseline_best_original = None
    if original_candidates:
        best_score = max(float(item["score"]) for item in original_candidates)
        tied = sorted(
            (item for item in original_candidates if float(item["score"]) == best_score),
            key=lambda item: str(item["basename"]),
        )
        baseline_best_original = tied[0]["basename"]
    return {
        "schema_version": 1,
        "simulation_session_id": session_id,
        "run_id": run_id,
        "run_seed": seed,
        "post_index": post_index,
        "virtual_timestamp": virtual_epoch,
        "virtual_local_time": datetime.fromtimestamp(virtual_epoch).astimezone().isoformat(),
        "interval_since_previous": interval,
        "line_no": int(quote["line_no"]),
        "quote_hash": str(quote["quote_hash"]),
        "quote_text": str(quote["text"]),
        "quote_selection_weight": float(quote.get("weight", 0.0)),
        "seasonal_status": season_status,
        "seasonal_weight_multiplier": float(seasonal_multiplier),
        "selection_attempts": int(selection["attempts"]),
        "selection_phase": selection["selection_phase"],
        "production_source": image["image_source"],
        "production_image": image["basename"],
        "production_score": float(image["score"]),
        "production_score_components": image.get("components", {}),
        "origin_quote_match": bool(image.get("origin_quote_match")),
        "origin_quote_boost": float(image.get("origin_quote_boost", 0.0)),
        "candidate_count": len(scored),
        "original_candidate_count": sum(item["image_source"] == "original" for item in scored),
        "generated_candidate_count": sum(item["image_source"] == "generated" for item in scored),
        "baseline_best_original": baseline_best_original,
        "diagnostic_candidate_present": any(item["basename"] == DIAGNOSTIC_IMAGE for item in scored),
        "diagnostic_candidate_origin_match": any(
            item["basename"] == DIAGNOSTIC_IMAGE and item.get("origin_quote_match") for item in scored
        ),
        "generated_spacing_counter_before": spacing_before,
        "generated_spacing_allowed_before": spacing_allowed_before,
        "generated_spacing_counter_after": spacing_after,
        "image_cycle_reset": image_cycle_reset,
        "quote_cycle_reset": quote_cycle_reset,
        "original_editorial_shadow": selection.get("original_editorial_shadow"),
        "generated_identity_shadow": selection.get("generated_identity_shadow"),
        "candidate_detail": candidate_detail_rows(bot, selection, candidate_detail),
        "image_traces": image_traces,
    }


def run_future(
    bot: Any,
    writer: PrivateWriter,
    session_dir: Path,
    snapshot: Path,
    session_id: str,
    run_index: int,
    seed: int,
    posts_per_run: int,
    start_epoch: int,
    candidate_detail: str,
    resume: bool,
    trace_images: tuple[str, ...] = (),
    stop_after_post: int | None = None,
    failure_hook: Any = None,
) -> list[dict]:
    """Run future."""
    run_id = f"run_{run_index:04d}"
    run_dir = writer.mkdir(session_dir / "runs" / run_id)
    checkpoint_path = run_dir / "checkpoint.json"
    output_path = run_dir / "selections.jsonl"
    configure_snapshot_paths(bot, snapshot, run_dir)
    install_hard_guards(bot, writer)

    if resume and checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        state = checkpoint["state"]
        images_used = set(checkpoint["images_used"])
        lines_used = set(checkpoint["lines_used"])
        completed = int(checkpoint["completed_posts"])
        virtual_epoch = int(checkpoint["virtual_epoch"])
        bot.random.setstate(rng_state_decode(checkpoint["rng_state"]))
        reconcile_jsonl_to_checkpoint(writer, output_path, completed)
        persist_private_state(writer, run_dir, state, images_used, lines_used)
    else:
        if output_path.exists():
            raise FileExistsError(f"run output already exists without --resume: {output_path}")
        state, images_used, lines_used = load_private_state(snapshot)
        completed = 0
        virtual_epoch = start_epoch
        bot.random.setstate(random.Random(seed).getstate())
        persist_private_state(writer, run_dir, state, images_used, lines_used)
        writer.atomic_json(
            checkpoint_path,
            checkpoint_payload(run_id, seed, 0, None, virtual_epoch, bot.random.getstate(), state, images_used, lines_used),
        )

    records: list[dict] = []
    for post_index in range(completed + 1, posts_per_run + 1):
        lines_before = set(lines_used)
        images_before = set(images_used)
        state_before = copy.deepcopy(state)
        spacing_before = bot.original_posts_since_generated_image(state)
        spacing_allowed = bot.generated_images_allowed_by_spacing(state)
        current_epoch = virtual_epoch
        bot.now_epoch = lambda epoch=current_epoch: epoch

        selection = select_with_production_recovery(bot, lines_used, images_used, state)
        quote_cycle_reset = bool(lines_before - lines_used)
        image_cycle_reset = bool(images_before - images_used) or bool(selection["image"].get("cycle_reset"))
        next_epoch = apply_simulated_success(
            bot, selection, state, lines_used, images_used, current_epoch, run_id, post_index
        )
        spacing_after = bot.original_posts_since_generated_image(state)
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        previous_epoch = checkpoint.get("last_post_epoch")
        interval = 0 if previous_epoch is None else current_epoch - int(previous_epoch)
        image_traces = [
            trace_image_lifecycle(
                bot,
                basename,
                selection,
                images_before,
                state_before,
                image_cycle_reset,
                spacing_allowed,
            )
            for basename in trace_images
        ]
        record = make_selection_record(
            bot,
            session_id,
            run_id,
            seed,
            post_index,
            current_epoch,
            interval,
            selection,
            spacing_before,
            spacing_allowed,
            spacing_after,
            quote_cycle_reset,
            image_cycle_reset,
            candidate_detail,
            image_traces,
        )
        writer.append_jsonl(output_path, record)
        if failure_hook:
            failure_hook("after_record_append", post_index)
        new_checkpoint = checkpoint_payload(
            run_id,
            seed,
            post_index,
            current_epoch,
            next_epoch,
            bot.random.getstate(),
            state,
            images_used,
            lines_used,
        )
        writer.atomic_json(checkpoint_path, new_checkpoint)
        if failure_hook:
            failure_hook("after_checkpoint", post_index)
        persist_private_state(writer, run_dir, state, images_used, lines_used)
        records.append(record)
        virtual_epoch = next_epoch
        if stop_after_post is not None and post_index >= stop_after_post:
            break
    return records


def load_all_records(session_dir: Path) -> list[dict]:
    """Load all records."""
    records: list[dict] = []
    for path in sorted((session_dir / "runs").glob("run_*/selections.jsonl")):
        with path.open(encoding="utf-8") as handle:
            records.extend(json.loads(line) for line in handle if line.strip())
    return records


BRANCHES = ("production", "editorial", "identity")


def counterfactual_paths(run_dir: Path) -> dict[str, Path]:
    """Return the counterfactual paths."""
    base = run_dir
    return {
        "shared": base / "shared_quotes.jsonl",
        "comparison": base / "branch_comparison.jsonl",
        **{branch: base / branch / "selections.jsonl" for branch in BRANCHES},
    }


def counterfactual_candidate_detail(rows: list[dict], mode: str) -> list[dict]:
    """Return the counterfactual candidate detail."""
    if mode == "none":
        return []
    ordered = sorted(
        rows,
        key=lambda row: (
            bool(row.get("policy_excluded")),
            -float(row["policy_score"]) if row.get("policy_score") is not None else math.inf,
            str(row["basename"]),
        ),
    )
    if mode == "top10":
        ordered = ordered[:10]
    return [
        {
            "basename": row["basename"],
            "source": row["image_source"],
            "baseline_score": float(row["baseline_score"]),
            "policy_score": row.get("policy_score"),
            "policy_excluded": bool(row.get("policy_excluded")),
            "editorial_adjustment": float(row.get("editorial_adjustment", 0.0)),
            "identity_policy": row.get("identity_policy"),
            "identity_action": row.get("identity_action"),
            "origin_quote_match": bool(row.get("origin_quote_match")),
        }
        for row in ordered
    ]


def counterfactual_branch_record(
    branch: str,
    selection: dict,
    run_id: str,
    run_seed: int,
    post_index: int,
    virtual_epoch: int,
    spacing_before: int,
    spacing_after: int,
    image_cycle_reset: bool,
    candidate_detail: str,
) -> dict:
    """Return the counterfactual branch record."""
    winner = selection["image"]
    rows = selection["scored"]
    return {
        "schema_version": 1,
        "branch": branch,
        "run_id": run_id,
        "run_seed": run_seed,
        "post_index": post_index,
        "virtual_timestamp": virtual_epoch,
        "selection_phase": selection["selection_phase"],
        "winner": winner["basename"],
        "winner_source": winner["image_source"],
        "baseline_score": float(winner.get("baseline_score", winner["score"])),
        "policy_score": float(winner.get("policy_score", winner["score"])),
        "editorial_adjustment": float(winner.get("editorial_adjustment", 0.0)),
        "editorial_cap_hit": bool((winner.get("policy_detail") or {}).get("cap_hit")),
        "origin_quote_match": bool(winner.get("origin_quote_match")),
        "origin_quote_boost": float(winner.get("origin_quote_boost", 0.0)),
        "identity_policy": winner.get("identity_policy"),
        "identity_action": winner.get("identity_action"),
        "identity_adjustment": winner.get("identity_adjustment", 0.0),
        "candidate_count": len(rows),
        "original_candidate_count": sum(row["image_source"] == "original" for row in rows),
        "generated_candidate_count": sum(row["image_source"] == "generated" for row in rows),
        "policy_excluded_count": sum(bool(row.get("policy_excluded")) for row in rows),
        "generated_spacing_counter_before": spacing_before,
        "generated_spacing_counter_after": spacing_after,
        "image_cycle_reset": image_cycle_reset,
        "diagnostic_candidate_present": any(row["basename"] == DIAGNOSTIC_IMAGE for row in rows),
        "diagnostic_candidate_origin_match": any(
            row["basename"] == DIAGNOSTIC_IMAGE and row.get("origin_quote_match") for row in rows
        ),
        "diagnostic_candidate_excluded": any(
            row["basename"] == DIAGNOSTIC_IMAGE and row.get("policy_excluded") for row in rows
        ),
        "candidate_detail": counterfactual_candidate_detail(rows, candidate_detail),
    }


def counterfactual_checkpoint_payload(
    run_id: str,
    run_seed: int,
    completed_posts: int,
    virtual_epoch: int,
    branches: dict[str, dict],
) -> dict:
    """Return the counterfactual checkpoint payload."""
    return {
        "schema_version": 1,
        "run_id": run_id,
        "run_seed": run_seed,
        "completed_posts": completed_posts,
        "virtual_epoch": virtual_epoch,
        "branches": {
            name: {
                "state": value["state"],
                "images_used": sorted(value["images_used"]),
                "lines_used": sorted(value["lines_used"]),
                "rng_state": rng_state_encode(value["rng_state"]),
            }
            for name, value in branches.items()
        },
    }


def restore_counterfactual_branches(checkpoint: dict) -> dict[str, dict]:
    """Restore counterfactual branches."""
    return {
        name: {
            "state": copy.deepcopy(value["state"]),
            "images_used": set(value["images_used"]),
            "lines_used": set(value["lines_used"]),
            "rng_state": rng_state_decode(value["rng_state"]),
        }
        for name, value in checkpoint["branches"].items()
    }


def reconcile_counterfactual_outputs(
    writer: PrivateWriter,
    paths: dict[str, Path],
    completed_posts: int,
) -> None:
    """Reconcile counterfactual outputs."""
    for path in paths.values():
        reconcile_jsonl_to_checkpoint(writer, path, completed_posts)


def run_counterfactual_future(
    bot: Any,
    writer: PrivateWriter,
    session_dir: Path,
    snapshot: Path,
    session_id: str,
    run_index: int,
    run_seed: int,
    posts_per_run: int,
    start_epoch: int,
    candidate_detail: str,
    resume: bool,
    *,
    stop_after_post: int | None = None,
    failure_hook: Any = None,
) -> None:
    """Run counterfactual future."""
    run_id = f"run_{run_index:04d}"
    run_dir = writer.mkdir(session_dir / "counterfactual" / "runs" / run_id)
    paths = counterfactual_paths(run_dir)
    for path in paths.values():
        writer.mkdir(path.parent)
    checkpoint_path = run_dir / "counterfactual_checkpoint.json"
    configure_snapshot_paths(bot, snapshot, run_dir)
    install_hard_guards(bot, writer)

    if resume and checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        completed = int(checkpoint["completed_posts"])
        virtual_epoch = int(checkpoint["virtual_epoch"])
        branches = restore_counterfactual_branches(checkpoint)
        reconcile_counterfactual_outputs(writer, paths, completed)
    else:
        if any(path.exists() for path in paths.values()):
            raise FileExistsError(f"counterfactual output exists without --resume: {run_dir}")
        initial_state, initial_images, initial_lines = load_private_state(snapshot)
        branches = {}
        for branch in BRANCHES:
            seed = run_seed if branch == "production" else derived_branch_seed(run_seed, branch)
            branches[branch] = {
                "state": copy.deepcopy(initial_state),
                "images_used": set(initial_images),
                "lines_used": set(initial_lines),
                "rng_state": random.Random(seed).getstate(),
            }
        completed = 0
        virtual_epoch = start_epoch
        writer.atomic_json(
            checkpoint_path,
            counterfactual_checkpoint_payload(run_id, run_seed, 0, virtual_epoch, branches),
        )

    comparison_history = reconcile_jsonl_to_checkpoint(writer, paths["comparison"], completed)
    for post_index in range(completed + 1, posts_per_run + 1):
        current_epoch = virtual_epoch
        bot.now_epoch = lambda epoch=current_epoch: epoch
        selections: dict[str, dict] = {}
        before: dict[str, dict] = {}

        production = branches["production"]
        bot.random.setstate(production["rng_state"])
        before["production"] = {
            "images": set(production["images_used"]),
            "spacing": bot.original_posts_since_generated_image(production["state"]),
        }
        selections["production"] = select_with_production_recovery(
            bot,
            production["lines_used"],
            production["images_used"],
            production["state"],
            evaluate_shadows=False,
        )
        production["rng_state"] = bot.random.getstate()
        shared_quote = selections["production"]["quote"]

        for branch in ("editorial", "identity"):
            value = branches[branch]
            value["lines_used"].clear()
            value["lines_used"].update(production["lines_used"])
            before[branch] = {
                "images": set(value["images_used"]),
                "spacing": bot.original_posts_since_generated_image(value["state"]),
            }
            bot.random.setstate(value["rng_state"])
            selections[branch] = select_policy_image_with_recovery(
                bot, shared_quote, value["images_used"], value["state"], branch
            )
            value["rng_state"] = bot.random.getstate()
            if failure_hook:
                failure_hook(f"after_{branch}_selection", post_index)

        bot.random.setstate(production["rng_state"])
        shared_quote_delay = bot.random.randint(bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX)
        shared_meme_delay = None
        if bot.ENABLE_DAILY_MEME_POSTS:
            shared_meme_delay = bot.random.randint(
                bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
                bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
            )
        next_epoch = apply_simulated_success(
            bot,
            selections["production"],
            production["state"],
            production["lines_used"],
            production["images_used"],
            current_epoch,
            f"{run_id}-production",
            post_index,
            quote_delay=shared_quote_delay,
            meme_delay=shared_meme_delay,
        )
        production["rng_state"] = bot.random.getstate()
        shared_delay = next_epoch - current_epoch

        branch_records: dict[str, dict] = {}
        for branch in ("editorial", "identity"):
            value = branches[branch]
            bot.random.setstate(value["rng_state"])
            apply_simulated_success(
                bot,
                selections[branch],
                value["state"],
                value["lines_used"],
                value["images_used"],
                current_epoch,
                f"{run_id}-{branch}",
                post_index,
                quote_delay=shared_delay,
                meme_delay=shared_meme_delay,
            )
            value["rng_state"] = bot.random.getstate()

        for branch in BRANCHES:
            value = branches[branch]
            selection = selections[branch]
            winner = selection["image"]
            if branch == "production":
                winner["baseline_score"] = float(winner["score"])
                winner["policy_score"] = float(winner["score"])
                winner["editorial_adjustment"] = 0.0
                winner["identity_adjustment"] = 0.0
                winner["identity_action"] = "production_baseline"
                rows = policy_candidate_rows(bot, shared_quote, selection["scored"], "production")
                selection = dict(selection)
                selection["scored"] = rows
                selection["image"] = next(row for row in rows if row["basename"] == winner["basename"])
                selections[branch] = selection
            image_cycle_reset = bool(before[branch]["images"] - value["images_used"]) or bool(
                selection["image"].get("cycle_reset")
            )
            branch_records[branch] = counterfactual_branch_record(
                branch,
                selection,
                run_id,
                run_seed,
                post_index,
                current_epoch,
                int(before[branch]["spacing"]),
                bot.original_posts_since_generated_image(value["state"]),
                image_cycle_reset,
                candidate_detail,
            )

        winners = {branch: branch_records[branch]["winner"] for branch in BRANCHES}
        production_rows = selections["production"]["scored"]
        observational_editorial_rows = policy_candidate_rows(bot, shared_quote, production_rows, "editorial")
        observational_identity_rows = policy_candidate_rows(bot, shared_quote, production_rows, "identity")
        observational_editorial = observational_policy_winner(observational_editorial_rows, winners["production"])
        observational_identity = observational_policy_winner(observational_identity_rows, winners["production"])
        identity_relevant_actions = {
            "generated_cross_quote_small_penalty",
            "generated_cross_quote_strong_penalty",
            "generated_cross_quote_origin_only_excluded",
        }
        identity_production_candidate = next(
            (row for row in selections["identity"]["scored"] if row["basename"] == winners["production"]),
            None,
        )
        first_divergence = not any(
            record.get("production_editorial_same") is False or record.get("production_identity_same") is False
            for record in comparison_history
        )
        first_production_editorial = not any(not record["production_editorial_same"] for record in comparison_history)
        first_production_identity = not any(not record["production_identity_same"] for record in comparison_history)
        first_editorial_identity = not any(not record["editorial_identity_same"] for record in comparison_history)
        production_editorial_same = winners["production"] == winners["editorial"]
        production_identity_same = winners["production"] == winners["identity"]
        editorial_identity_same = winners["editorial"] == winners["identity"]
        comparison = {
            "schema_version": 1,
            "simulation_session_id": session_id,
            "run_id": run_id,
            "run_seed": run_seed,
            "post_index": post_index,
            "virtual_timestamp": current_epoch,
            "quote_hash": shared_quote["quote_hash"],
            "quote_text": shared_quote["text"],
            "quote_coupling": "shared",
            "winners": winners,
            "winner_sources": {branch: branch_records[branch]["winner_source"] for branch in BRANCHES},
            "production_editorial_same": production_editorial_same,
            "production_identity_same": production_identity_same,
            "editorial_identity_same": editorial_identity_same,
            "all_three_same": len(set(winners.values())) == 1,
            "first_any_divergence": first_divergence and len(set(winners.values())) > 1,
            "first_production_editorial_divergence": first_production_editorial and not production_editorial_same,
            "first_production_identity_divergence": first_production_identity and not production_identity_same,
            "first_editorial_identity_divergence": first_editorial_identity and not editorial_identity_same,
            "cumulative_production_editorial_divergences": sum(not row["production_editorial_same"] for row in comparison_history) + int(not production_editorial_same),
            "cumulative_production_identity_divergences": sum(not row["production_identity_same"] for row in comparison_history) + int(not production_identity_same),
            "cumulative_editorial_identity_divergences": sum(not row["editorial_identity_same"] for row in comparison_history) + int(not editorial_identity_same),
            "observational_editorial_winner": observational_editorial,
            "observational_identity_winner": observational_identity,
            "observational_editorial_changed": observational_editorial != winners["production"],
            "observational_identity_changed": observational_identity != winners["production"],
            "observational_editorial_comparable": branch_records["production"]["winner_source"] == "original",
            "observational_identity_policy_relevant": any(
                row.get("identity_action") in identity_relevant_actions for row in observational_identity_rows
            ),
            "production_candidate_identity_action": (
                identity_production_candidate.get("identity_action") if identity_production_candidate else "not_in_identity_candidate_set"
            ),
            "production_candidate_identity_excluded": bool(
                identity_production_candidate and identity_production_candidate.get("policy_excluded")
            ),
        }
        shared = {
            "schema_version": 1,
            "post_index": post_index,
            "virtual_timestamp": current_epoch,
            "quote_hash": shared_quote["quote_hash"],
            "quote_text": shared_quote["text"],
            "line_no": shared_quote["line_no"],
            "weight": shared_quote.get("weight"),
        }
        writer.append_jsonl(paths["shared"], shared)
        for branch in BRANCHES:
            writer.append_jsonl(paths[branch], branch_records[branch])
        writer.append_jsonl(paths["comparison"], comparison)
        comparison_history.append(comparison)
        if failure_hook:
            failure_hook("after_comparison_append", post_index)

        virtual_epoch = next_epoch
        writer.atomic_json(
            checkpoint_path,
            counterfactual_checkpoint_payload(run_id, run_seed, post_index, virtual_epoch, branches),
        )
        if failure_hook:
            failure_hook("after_counterfactual_checkpoint", post_index)
        if stop_after_post is not None and post_index >= stop_after_post:
            break


def entropy(counter: Counter[str]) -> float:
    """Return the entropy."""
    total = sum(counter.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counter.values())


def variation(values: list[float]) -> dict:
    """Return the variation."""
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def five_number(values: list[float]) -> dict:
    """Return the five number."""
    if not values:
        return {"min": 0.0, "lower_quartile": 0.0, "median": 0.0, "upper_quartile": 0.0, "max": 0.0}
    ordered = sorted(values)
    quartiles = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 else [ordered[0]] * 3
    return {
        "min": ordered[0],
        "lower_quartile": quartiles[0],
        "median": statistics.median(ordered),
        "upper_quartile": quartiles[2],
        "max": ordered[-1],
    }


def concentration(counter: Counter[str], denominator: int | None = None) -> dict:
    """Return the concentration."""
    total = sum(counter.values()) if denominator is None else denominator
    return {
        "observations": total,
        "entropy_bits": entropy(counter),
        "top10_share": sum(count for _, count in counter.most_common(10)) / total if total else 0.0,
        "most_frequent": counter.most_common(15),
    }


def summarize_records(records: list[dict], runtime_seconds: float) -> dict:
    """Summarise records."""
    production = Counter(record["production_source"] for record in records)
    images = Counter(record["production_image"] for record in records)
    phases = Counter(record["selection_phase"] for record in records)
    origin = sum(record["production_source"] == "generated" and record["origin_quote_match"] for record in records)
    cross = sum(record["production_source"] == "generated" and not record["origin_quote_match"] for record in records)

    editorial_events = [record["original_editorial_shadow"] for record in records if record.get("original_editorial_shadow")]
    editorial_comparable = [event for event in editorial_events if event.get("production_shadow_rank") is not None]
    editorial_changed = [event for event in editorial_comparable if event.get("winner_changed")]
    editorial_adjustments = [float(event["production_editorial_adjustment"]) for event in editorial_comparable]
    editorial_production_winners = Counter(event["production_winner"] for event in editorial_comparable)
    editorial_winners = Counter(event["shadow_original_winner"] for event in editorial_comparable)
    editorial_transitions = Counter(
        f"{event['production_winner']} -> {event['shadow_original_winner']}"
        for event in editorial_changed
    )
    per_image_editorial = []
    for basename in sorted(set(editorial_production_winners) | set(editorial_winners)):
        production_count = editorial_production_winners[basename]
        shadow_count = editorial_winners[basename]
        per_image_editorial.append(
            {
                "basename": basename,
                "production_wins": production_count,
                "shadow_preferences": shadow_count,
                "net_observational_preference": shadow_count - production_count,
            }
        )
    per_image_editorial.sort(key=lambda row: (-abs(row["net_observational_preference"]), row["basename"]))

    repeated_by_image: Counter[str] = Counter()
    repeated_comparable_by_image: Counter[str] = Counter()
    outstanding_preferences: dict[tuple[str, str], int] = defaultdict(int)
    outstanding_comparable: dict[tuple[str, str], int] = defaultdict(int)
    for record in records:
        run_id = record["run_id"]
        production_image = record["production_image"]
        outstanding_preferences[(run_id, production_image)] = 0
        outstanding_comparable[(run_id, production_image)] = 0
        event = record.get("original_editorial_shadow") or {}
        shadow = str(event.get("shadow_original_winner") or "")
        if not shadow or shadow == production_image:
            continue
        key = (run_id, shadow)
        if outstanding_preferences[key] > 0:
            repeated_by_image[shadow] += 1
        outstanding_preferences[key] += 1
        if event.get("production_shadow_rank") is not None and event.get("winner_changed"):
            if outstanding_comparable[key] > 0:
                repeated_comparable_by_image[shadow] += 1
            outstanding_comparable[key] += 1

    baseline_original_records = [
        record
        for record in records
        if record.get("baseline_best_original") and (record.get("original_editorial_shadow") or {}).get("shadow_original_winner")
    ]
    baseline_original_winners = Counter(record["baseline_best_original"] for record in baseline_original_records)
    adjusted_original_winners = Counter(record["original_editorial_shadow"]["shadow_original_winner"] for record in baseline_original_records)

    identity_events = [record["generated_identity_shadow"] for record in records if record.get("generated_identity_shadow")]
    identity_relevant = [
        event
        for event in identity_events
        if int(event.get("small_penalty_count", 0))
        + int(event.get("strong_penalty_count", 0))
        + int(event.get("origin_quote_only_excluded_count", 0))
        > 0
    ]
    identity_changed = [event for event in identity_relevant if event.get("winner_changed")]

    diagnostic = {
        "candidate_sets": 0,
        "origin_candidate_sets": 0,
        "cross_quote_candidate_sets": 0,
        "production_cross_quote_wins": 0,
        "identity_shadow_exclusions": 0,
        "replacement_winners": Counter(),
    }
    for record in records:
        if record.get("diagnostic_candidate_present"):
            diagnostic["candidate_sets"] += 1
            if record.get("diagnostic_candidate_origin_match"):
                diagnostic["origin_candidate_sets"] += 1
            else:
                diagnostic["cross_quote_candidate_sets"] += 1
        if record["production_image"] == DIAGNOSTIC_IMAGE and not record["origin_quote_match"]:
            diagnostic["production_cross_quote_wins"] += 1
        event = record.get("generated_identity_shadow") or {}
        if DIAGNOSTIC_IMAGE in event.get("excluded_generated_basenames", []):
            diagnostic["identity_shadow_exclusions"] += 1
            if event.get("production_winner") == DIAGNOSTIC_IMAGE and event.get("shadow_winner"):
                diagnostic["replacement_winners"][event["shadow_winner"]] += 1
    diagnostic["replacement_winners"] = diagnostic["replacement_winners"].most_common(10)

    per_run: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        per_run[record["run_id"]].append(record)
    generated_rates: list[float] = []
    editorial_rates: list[float] = []
    identity_rates: list[float] = []
    for run_records in per_run.values():
        generated_rates.append(sum(r["production_source"] == "generated" for r in run_records) / len(run_records))
        comp = [r["original_editorial_shadow"] for r in run_records if (r.get("original_editorial_shadow") or {}).get("production_shadow_rank") is not None]
        editorial_rates.append(sum(e.get("winner_changed") for e in comp) / len(comp) if comp else 0.0)
        relevant = [
            r["generated_identity_shadow"]
            for r in run_records
            if sum(int((r.get("generated_identity_shadow") or {}).get(key, 0)) for key in ("small_penalty_count", "strong_penalty_count", "origin_quote_only_excluded_count")) > 0
        ]
        identity_rates.append(sum(e.get("winner_changed") for e in relevant) / len(relevant) if relevant else 0.0)

    identity_replacements = Counter(
        event.get("shadow_winner_source") or "none" for event in identity_changed
    )
    identity_production_winners = Counter(
        event["production_winner"] for event in identity_events if event.get("production_winner")
    )
    identity_winners = Counter(
        event["shadow_winner"] for event in identity_events if event.get("shadow_winner")
    )
    identity_source_transitions = Counter(
        f"{event.get('production_source') or 'unknown'} -> {event.get('shadow_winner_source') or 'none'}"
        for event in identity_events
    )
    scores = [float(record["production_score"]) for record in records]
    trace_names = sorted({trace["basename"] for record in records for trace in record.get("image_traces", [])})
    trace_summary: dict[str, dict] = {}
    for basename in trace_names:
        traces = [trace for record in records for trace in record.get("image_traces", []) if trace["basename"] == basename]
        trace_summary[basename] = {
            "observations": len(traces),
            "candidate_discovery_opportunities": sum(bool(trace.get("discovered")) for trace in traces),
            "generated_spacing_allowed": sum(bool(trace.get("generated_spacing_allowed")) for trace in traces),
            "origin_quote_opportunities": sum(bool(trace.get("origin_quote_match")) for trace in traces),
            "cross_quote_opportunities": sum(not bool(trace.get("origin_quote_match")) for trace in traces),
            "reached_scored_candidates": sum(bool(trace.get("reached_scored_candidates")) for trace in traces),
            "reached_scored_as_origin_quote": sum(bool(trace.get("reached_scored_candidates")) and bool(trace.get("origin_quote_match")) for trace in traces),
            "reached_scored_as_cross_quote": sum(bool(trace.get("reached_scored_candidates")) and not bool(trace.get("origin_quote_match")) for trace in traces),
            "production_wins": sum(bool(trace.get("production_winner")) for trace in traces),
            "reasons": dict(Counter(str(trace.get("reason")) for trace in traces)),
        }
    return {
        "schema_version": 2,
        "total_selections": len(records),
        "runtime_seconds": runtime_seconds,
        "selections_per_second": len(records) / runtime_seconds if runtime_seconds else 0.0,
        "run_count": len(per_run),
        "posts_per_run": sorted({len(value) for value in per_run.values()}),
        "virtual_time": {
            "start": min((r["virtual_timestamp"] for r in records), default=None),
            "end": max((r["virtual_timestamp"] for r in records), default=None),
        },
        "production": {
            "original_winners": production["original"],
            "generated_winners": production["generated"],
            "generated_share": production["generated"] / len(records) if records else 0.0,
            "generated_origin_matches": origin,
            "generated_cross_quote": cross,
            "selection_phases": dict(phases),
            "quote_cycle_resets": sum(r["quote_cycle_reset"] for r in records),
            "image_cycle_resets": sum(r["image_cycle_reset"] for r in records),
            "score": {
                "min": min(scores) if scores else None,
                "median": statistics.median(scores) if scores else None,
                "max": max(scores) if scores else None,
            },
            "most_frequent_images": images.most_common(15),
            "whole_distribution": concentration(images, len(records)),
        },
        "original_editorial_shadow": {
            "observations": len(editorial_events),
            "comparable_original_observations": len(editorial_comparable),
            "winner_changes": len(editorial_changed),
            "unchanged_observations": len(editorial_comparable) - len(editorial_changed),
            "winner_change_rate": len(editorial_changed) / len(editorial_comparable) if editorial_comparable else 0.0,
            "production_rank_distribution": dict(Counter(str(event["production_shadow_rank"]) for event in editorial_comparable)),
            "adjustment_mean": statistics.mean(editorial_adjustments) if editorial_adjustments else 0.0,
            "adjustment_median": statistics.median(editorial_adjustments) if editorial_adjustments else 0.0,
            "cap_hits": sum(bool(event.get("cap_hit")) for event in editorial_events),
            "most_frequent_shadow_preferences_all_original_candidate_observations": Counter(event["shadow_original_winner"] for event in editorial_events).most_common(15),
            "most_frequent_gaining_images": Counter(event["shadow_original_winner"] for event in editorial_changed).most_common(15),
            "most_frequent_losing_production_winners": Counter(event["production_winner"] for event in editorial_changed).most_common(15),
            "replacement_matrix": editorial_transitions.most_common(30),
            "matched_comparable_distribution": {
                "denominator": len(editorial_comparable),
                "production_original": concentration(editorial_production_winners, len(editorial_comparable)),
                "editorial_shadow_preference": concentration(editorial_winners, len(editorial_comparable)),
            },
            "best_original_matched_distribution": {
                "denominator": len(baseline_original_records),
                "baseline_best_original": concentration(baseline_original_winners, len(baseline_original_records)),
                "editorial_adjusted_best_original": concentration(adjusted_original_winners, len(baseline_original_records)),
            },
            "per_image_observational_preference": per_image_editorial,
            "repeated_observational_preference_count": sum(repeated_by_image.values()),
            "repeated_observational_preference_by_image": repeated_by_image.most_common(15),
            "repeated_comparable_preference_count": sum(repeated_comparable_by_image.values()),
            "repeated_comparable_preference_by_image": repeated_comparable_by_image.most_common(15),
            "per_run_change_rate": variation(editorial_rates),
        },
        "generated_identity_shadow": {
            "observations": len(identity_events),
            "policy_relevant_observations": len(identity_relevant),
            "winner_changes": len(identity_changed),
            "winner_change_rate": len(identity_changed) / len(identity_relevant) if identity_relevant else 0.0,
            "production_origin_only_excluded": sum(event.get("production_identity_action") == "generated_cross_quote_origin_only_excluded" for event in identity_events),
            "production_small_penalty": sum(event.get("production_identity_action") == "generated_cross_quote_small_penalty" for event in identity_events),
            "production_strong_penalty": sum(event.get("production_identity_action") == "generated_cross_quote_strong_penalty" for event in identity_events),
            "excluded_candidate_appearances": sum(int(event.get("origin_quote_only_excluded_count", 0)) for event in identity_events),
            "penalised_candidate_appearances": sum(int(event.get("small_penalty_count", 0)) + int(event.get("strong_penalty_count", 0)) for event in identity_events),
            "affected_candidates": sum(int(event.get("small_penalty_count", 0)) + int(event.get("strong_penalty_count", 0)) + int(event.get("origin_quote_only_excluded_count", 0)) for event in identity_events),
            "replacement_sources": dict(identity_replacements),
            "source_transition_matrix": dict(identity_source_transitions),
            "most_frequent_excluded": Counter(name for event in identity_events for name in event.get("excluded_generated_basenames", [])).most_common(15),
            "most_frequent_penalised": Counter(name for event in identity_events for name in event.get("penalised_generated_basenames", [])).most_common(15),
            "most_frequent_shadow_winners": Counter(event.get("shadow_winner") for event in identity_events if event.get("shadow_winner")).most_common(15),
            "matched_all_observation_distribution": {
                "denominator": len(identity_events),
                "production": concentration(identity_production_winners, len(identity_events)),
                "identity_shadow": concentration(identity_winners, len(identity_events)),
            },
            "per_run_change_rate": variation(identity_rates),
        },
        "diagnostic_image": diagnostic,
        "traced_images": trace_summary,
        "across_run_generated_share": variation(generated_rates),
        "interpretation": (
            "Alternate production-parity selection futures from one immutable state snapshot; "
            "not a forecast of future X activity or human interactions."
        ),
    }


def markdown_report(session_manifest: dict, summary: dict) -> str:
    """Return the markdown report."""
    prod = summary["production"]
    editorial = summary["original_editorial_shadow"]
    identity = summary["generated_identity_shadow"]
    diagnostic = summary["diagnostic_image"]
    matched = editorial["matched_comparable_distribution"]
    identity_matched = identity["matched_all_observation_distribution"]
    lines = [
        "# Accelerated Regular-Post Simulation Report",
        "",
        "> Observational shadow preferences along production-controlled state trajectories only. Shadow preferences do not evolve their own used-image state and no image was posted.",
        "",
        "## Simulation overview",
        "",
        f"- Runs: {summary['run_count']}",
        f"- Total selections: {summary['total_selections']}",
        f"- Seeds: {session_manifest['seed_base']} through {session_manifest['seed_base'] + session_manifest['runs'] - 1}",
        f"- Runtime: {summary['runtime_seconds']:.3f} seconds",
        f"- Throughput: {summary['selections_per_second']:.2f} selections/second",
        f"- Virtual epoch span: {summary['virtual_time']['start']} to {summary['virtual_time']['end']}",
        "",
        "## Production behavior",
        "",
        f"- Original winners: {prod['original_winners']}",
        f"- Generated winners: {prod['generated_winners']} ({prod['generated_share']:.1%})",
        f"- Generated origin matches: {prod['generated_origin_matches']}",
        f"- Generated cross-quote uses: {prod['generated_cross_quote']}",
        f"- Phases: {prod['selection_phases']}",
        f"- Quote-cycle resets: {prod['quote_cycle_resets']}",
        f"- Image-cycle resets: {prod['image_cycle_resets']}",
        f"- Whole-production winner entropy: {prod['whole_distribution']['entropy_bits']:.3f} bits; top-10 share {prod['whole_distribution']['top10_share']:.1%}",
        "",
        "## Original editorial shadow",
        "",
        f"- Observations: {editorial['observations']}",
        f"- Comparable production-original observations: {editorial['comparable_original_observations']}",
        f"- Winner changes: {editorial['winner_changes']} ({editorial['winner_change_rate']:.1%} of comparable observations)",
        f"- Unchanged comparable observations: {editorial['unchanged_observations']}",
        f"- Mean/median production-winner adjustment: {editorial['adjustment_mean']:.3f} / {editorial['adjustment_median']:.3f}",
        f"- Cap-hit observations: {editorial['cap_hits']}",
        f"- Matched production-original entropy/top-10 share: {matched['production_original']['entropy_bits']:.3f} bits / {matched['production_original']['top10_share']:.1%}",
        f"- Matched editorial-preference entropy/top-10 share: {matched['editorial_shadow_preference']['entropy_bits']:.3f} bits / {matched['editorial_shadow_preference']['top10_share']:.1%}",
        f"- Repeated observational preferences across all original-candidate opportunities: {editorial['repeated_observational_preference_count']}",
        f"- Repeated preferences within comparable production-original observations: {editorial['repeated_comparable_preference_count']}",
        f"- Most frequent observational gains: {editorial['most_frequent_gaining_images']}",
        f"- Across-run change rate: {editorial['per_run_change_rate']}",
        "",
        "## Generated identity-policy shadow",
        "",
        f"- Observations: {identity['observations']}",
        f"- Policy-relevant observations: {identity['policy_relevant_observations']}",
        f"- Winner changes: {identity['winner_changes']} ({identity['winner_change_rate']:.1%} of policy-relevant observations)",
        f"- Production origin-only exclusions: {identity['production_origin_only_excluded']}",
        f"- Production small/strong penalties: {identity['production_small_penalty']} / {identity['production_strong_penalty']}",
        f"- Affected candidate appearances: {identity['affected_candidates']}",
        f"- Excluded/penalised candidate appearances: {identity['excluded_candidate_appearances']} / {identity['penalised_candidate_appearances']}",
        f"- Replacement sources: {identity['replacement_sources']}",
        f"- Production/shadow source transitions: {identity['source_transition_matrix']}",
        f"- Matched production entropy/top-10 share: {identity_matched['production']['entropy_bits']:.3f} bits / {identity_matched['production']['top10_share']:.1%}",
        f"- Matched identity-shadow entropy/top-10 share: {identity_matched['identity_shadow']['entropy_bits']:.3f} bits / {identity_matched['identity_shadow']['top10_share']:.1%}",
        f"- Across-run change rate: {identity['per_run_change_rate']}",
        "",
        "## Diagnostic image",
        "",
        f"`{DIAGNOSTIC_IMAGE}`",
        "",
        f"- Candidate-set appearances recorded: {diagnostic['candidate_sets']}",
        f"- Origin/cross-quote candidate appearances: {diagnostic['origin_candidate_sets']} / {diagnostic['cross_quote_candidate_sets']}",
        f"- Production cross-quote wins: {diagnostic['production_cross_quote_wins']}",
        f"- Identity-shadow exclusions: {diagnostic['identity_shadow_exclusions']}",
        f"- Replacement winners when it was the excluded production winner: {diagnostic['replacement_winners']}",
        f"- Lifecycle trace: {summary['traced_images'].get(DIAGNOSTIC_IMAGE, 'not requested')}",
        "",
        "## Across-run variability",
        "",
        f"- Generated production share: {summary['across_run_generated_share']}",
        f"- Editorial change rate: {editorial['per_run_change_rate']}",
        f"- Identity change rate: {identity['per_run_change_rate']}",
        "",
        "## Why counterfactual evolving branches are a separate experiment",
        "",
        "This simulator advances used histories with the production winner. If editorial shadow prefers `t10.jpg` while production selects `t49.jpg`, only `t49.jpg` becomes used; `t10.jpg` may therefore be preferred again later. A policy-controlled editorial branch would consume `t10.jpg` and create a different future candidate set. The same applies to an identity-policy branch when its winner differs. Such counterfactual branches require independent evolving state and are deliberately not implemented here.",
        "",
        "## Caveats",
        "",
        "This answers what each shadow policy prefers at opportunities along a production-controlled state trajectory. It does not forecast the image distribution that either shadow policy would produce if deployed. Observational gains, repeated preferences, and entropy are not counterfactual post counts. "
        "The simulator preserves current quote/image selection, cycles, spacing, scoring, RNG, virtual seasonal time, and shadow policies. "
        "It cannot forecast future human mentions, quote-posts, attached media, new assets, external X behavior, or future code/config changes. "
        "Runs are alternate RNG futures from one snapshot, not independent samples of human behavior.",
        "",
    ]
    return "\n".join(lines)


def load_counterfactual_records(session_dir: Path) -> tuple[dict[str, list[dict]], list[dict]]:
    """Load counterfactual records."""
    branch_records = {branch: [] for branch in BRANCHES}
    comparisons: list[dict] = []
    for run_dir in sorted((session_dir / "counterfactual" / "runs").glob("run_*")):
        paths = counterfactual_paths(run_dir)
        for branch in BRANCHES:
            if paths[branch].is_file():
                branch_records[branch].extend(
                    json.loads(line) for line in paths[branch].read_text(encoding="utf-8").splitlines() if line.strip()
                )
        if paths["comparison"].is_file():
            comparisons.extend(
                json.loads(line) for line in paths["comparison"].read_text(encoding="utf-8").splitlines() if line.strip()
            )
    return branch_records, comparisons


def branch_diversity(records: list[dict]) -> dict:
    """Return the branch diversity."""
    winners = Counter(record["winner"] for record in records)
    per_run_last: dict[tuple[str, str], int] = {}
    intervals: list[int] = []
    for record in records:
        key = (str(record.get("run_id") or ""), record["winner"])
        index = int(record["post_index"])
        if key in per_run_last:
            intervals.append(index - per_run_last[key])
        per_run_last[key] = index
    return {
        "unique_images": len(winners),
        "entropy_bits": entropy(winners),
        "top5_share": sum(count for _, count in winners.most_common(5)) / len(records) if records else 0.0,
        "top10_share": sum(count for _, count in winners.most_common(10)) / len(records) if records else 0.0,
        "maximum_image_count": max(winners.values(), default=0),
        "most_selected": winners.most_common(15),
        "reuse_interval_posts": {
            "count": len(intervals),
            "minimum": min(intervals) if intervals else None,
            "median": statistics.median(intervals) if intervals else None,
            "histogram": dict(sorted(Counter(intervals).items())),
        },
    }


def summarize_counterfactual(
    branch_records: dict[str, list[dict]],
    comparisons: list[dict],
    runtime_seconds: float,
) -> dict:
    """Summarise counterfactual."""
    total = len(comparisons)
    per_branch: dict[str, dict] = {}
    for branch, records in branch_records.items():
        original = sum(record["winner_source"] == "original" for record in records)
        generated = len(records) - original
        origin = sum(record["winner_source"] == "generated" and record["origin_quote_match"] for record in records)
        diagnostic_wins = [record for record in records if record["winner"] == DIAGNOSTIC_IMAGE]
        per_branch[branch] = {
            "selections": len(records),
            "original_winners": original,
            "generated_winners": generated,
            "generated_share": generated / len(records) if records else 0.0,
            "generated_origin_quote": origin,
            "generated_cross_quote": generated - origin,
            "selection_phases": dict(Counter(record["selection_phase"] for record in records)),
            "image_cycle_resets": sum(bool(record["image_cycle_reset"]) for record in records),
            "diversity": branch_diversity(records),
            "diagnostic": {
                "candidate_appearances": sum(bool(record["diagnostic_candidate_present"]) for record in records),
                "origin_candidate_appearances": sum(bool(record["diagnostic_candidate_origin_match"]) for record in records),
                "cross_quote_candidate_appearances": sum(bool(record["diagnostic_candidate_present"]) and not bool(record["diagnostic_candidate_origin_match"]) for record in records),
                "exclusions": sum(bool(record["diagnostic_candidate_excluded"]) for record in records),
                "wins": len(diagnostic_wins),
                "win_post_indices": [record["post_index"] for record in diagnostic_wins],
                "replacement_winners_when_production_selected_diagnostic": Counter(
                    row["winners"][branch]
                    for row in comparisons
                    if row["winners"]["production"] == DIAGNOSTIC_IMAGE
                    and row["winners"][branch] != DIAGNOSTIC_IMAGE
                ).most_common(10),
            },
        }
    editorial_records = branch_records["editorial"]
    identity_records = branch_records["identity"]
    editorial_original_adjustments = [
        float(record["editorial_adjustment"])
        for record in editorial_records
        if record["winner_source"] == "original"
    ]
    identity_actions = Counter(record.get("production_candidate_identity_action") for record in comparisons)
    observational_editorial_comparable = [row for row in comparisons if row.get("observational_editorial_comparable")]
    observational_identity_relevant = [row for row in comparisons if row.get("observational_identity_policy_relevant")]

    by_run: dict[str, list[dict]] = defaultdict(list)
    for comparison in comparisons:
        by_run[comparison["run_id"]].append(comparison)
    first_divergence: dict[str, dict] = {}
    editorial_rates: list[float] = []
    identity_rates: list[float] = []
    all_agreement_rates: list[float] = []
    generated_shares: dict[str, list[float]] = {branch: [] for branch in BRANCHES}
    for run_id, rows in by_run.items():
        first_divergence[run_id] = {
            "production_editorial": next((row["post_index"] for row in rows if not row["production_editorial_same"]), None),
            "production_identity": next((row["post_index"] for row in rows if not row["production_identity_same"]), None),
            "editorial_identity": next((row["post_index"] for row in rows if not row["editorial_identity_same"]), None),
        }
        editorial_rates.append(sum(not row["production_editorial_same"] for row in rows) / len(rows))
        identity_rates.append(sum(not row["production_identity_same"] for row in rows) / len(rows))
        all_agreement_rates.append(sum(row["all_three_same"] for row in rows) / len(rows))
        for branch in BRANCHES:
            run_branch = [record for record in branch_records[branch] if record.get("run_id") == run_id]
            generated_shares[branch].append(sum(record["winner_source"] == "generated" for record in run_branch) / len(run_branch))

    return {
        "schema_version": 1,
        "total_post_indices": total,
        "total_branch_selections": sum(len(records) for records in branch_records.values()),
        "runtime_seconds": runtime_seconds,
        "branch_selections_per_second": sum(len(records) for records in branch_records.values()) / runtime_seconds if runtime_seconds else 0.0,
        "quote_coupling": "shared",
        "run_count": len(by_run),
        "first_divergence": first_divergence,
        "agreement": {
            "all_three": sum(row["all_three_same"] for row in comparisons) / total if total else 0.0,
            "production_editorial": sum(row["production_editorial_same"] for row in comparisons) / total if total else 0.0,
            "production_identity": sum(row["production_identity_same"] for row in comparisons) / total if total else 0.0,
            "editorial_identity": sum(row["editorial_identity_same"] for row in comparisons) / total if total else 0.0,
        },
        "branches": per_branch,
        "editorial": {
            "divergences_from_production": sum(not row["production_editorial_same"] for row in comparisons),
            "divergence_rate": sum(not row["production_editorial_same"] for row in comparisons) / total if total else 0.0,
            "replacement_sources": dict(Counter(row["winner_sources"]["editorial"] for row in comparisons if not row["production_editorial_same"])),
            "selected_original_adjustment_mean": statistics.mean(editorial_original_adjustments) if editorial_original_adjustments else 0.0,
            "selected_original_adjustment_median": statistics.median(editorial_original_adjustments) if editorial_original_adjustments else 0.0,
            "cap_hits": sum(bool(record["editorial_cap_hit"]) for record in editorial_records),
            "observational_change_rate_on_production_trajectory": sum(row["observational_editorial_changed"] for row in comparisons) / total if total else 0.0,
            "observational_comparable_original_count": len(observational_editorial_comparable),
            "observational_comparable_original_change_rate": (
                sum(row["observational_editorial_changed"] for row in observational_editorial_comparable)
                / len(observational_editorial_comparable)
                if observational_editorial_comparable else 0.0
            ),
        },
        "identity": {
            "divergences_from_production": sum(not row["production_identity_same"] for row in comparisons),
            "divergence_rate": sum(not row["production_identity_same"] for row in comparisons) / total if total else 0.0,
            "replacement_sources": dict(Counter(row["winner_sources"]["identity"] for row in comparisons if not row["production_identity_same"])),
            "production_candidate_actions": dict(identity_actions),
            "production_candidates_origin_only_excluded": sum(bool(row["production_candidate_identity_excluded"]) for row in comparisons),
            "policy_excluded_candidate_appearances": sum(record["policy_excluded_count"] for record in identity_records),
            "observational_change_rate_on_production_trajectory": sum(row["observational_identity_changed"] for row in comparisons) / total if total else 0.0,
            "observational_policy_relevant_count": len(observational_identity_relevant),
            "observational_policy_relevant_change_rate": (
                sum(row["observational_identity_changed"] for row in observational_identity_relevant)
                / len(observational_identity_relevant)
                if observational_identity_relevant else 0.0
            ),
        },
        "across_run": {
            "editorial_divergence_rate": five_number(editorial_rates),
            "identity_divergence_rate": five_number(identity_rates),
            "all_three_agreement_rate": five_number(all_agreement_rates),
            "generated_share": {branch: five_number(values) for branch, values in generated_shares.items()},
        },
        "interpretation": (
            "Counterfactual image-policy state trajectories with shared production-selected quotes and virtual time; "
            "not a forecast of audience, engagement, future assets, or external X behavior."
        ),
    }


def counterfactual_markdown_report(session_manifest: dict, summary: dict) -> str:
    """Return the counterfactual markdown report."""
    lines = [
        "# Counterfactual Policy Branch Simulation Report",
        "",
        "> Offline policy-controlled image-state futures. No image was posted and no external API was called.",
        "",
        "## Overview",
        "",
        f"- Runs: {summary['run_count']}",
        f"- Matched post indices: {summary['total_post_indices']}",
        f"- Branch selections: {summary['total_branch_selections']}",
        f"- Quote coupling: {summary['quote_coupling']}",
        f"- RNG: production seed unchanged; editorial/identity SHA-256-derived independent streams",
        f"- Runtime: {summary['runtime_seconds']:.3f}s ({summary['branch_selections_per_second']:.2f} branch selections/s)",
        "",
        "## Agreement",
        "",
        f"- All three: {summary['agreement']['all_three']:.2%}",
        f"- Production/editorial: {summary['agreement']['production_editorial']:.2%}",
        f"- Production/identity: {summary['agreement']['production_identity']:.2%}",
        f"- Editorial/identity: {summary['agreement']['editorial_identity']:.2%}",
    ]
    for branch in BRANCHES:
        data = summary["branches"][branch]
        diversity = data["diversity"]
        lines.extend(
            [
                "",
                f"## {branch.title()} branch",
                "",
                f"- Original/generated: {data['original_winners']} / {data['generated_winners']} ({data['generated_share']:.2%} generated)",
                f"- Generated origin/cross-quote: {data['generated_origin_quote']} / {data['generated_cross_quote']}",
                f"- Phases: {data['selection_phases']}; image-cycle resets: {data['image_cycle_resets']}",
                f"- Unique images: {diversity['unique_images']}; entropy {diversity['entropy_bits']:.4f} bits",
                f"- Top-5/top-10 share: {diversity['top5_share']:.2%} / {diversity['top10_share']:.2%}",
                f"- Maximum image count: {diversity['maximum_image_count']}; reuse intervals {diversity['reuse_interval_posts']}",
                f"- Diagnostic image: {data['diagnostic']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Editorial policy effects",
            "",
            f"- Divergence from production: {summary['editorial']['divergences_from_production']} ({summary['editorial']['divergence_rate']:.2%})",
            f"- Observational preference change rate on production trajectory: {summary['editorial']['observational_change_rate_on_production_trajectory']:.2%}",
            f"- Observational comparable-original change rate: {summary['editorial']['observational_comparable_original_change_rate']:.2%} of {summary['editorial']['observational_comparable_original_count']}",
            f"- Replacement sources: {summary['editorial']['replacement_sources']}",
            f"- Selected-original adjustment mean/median: {summary['editorial']['selected_original_adjustment_mean']:.4f} / {summary['editorial']['selected_original_adjustment_median']:.4f}",
            f"- Cap hits: {summary['editorial']['cap_hits']}",
            "",
            "## Identity policy effects",
            "",
            f"- Divergence from production: {summary['identity']['divergences_from_production']} ({summary['identity']['divergence_rate']:.2%})",
            f"- Observational preference change rate on production trajectory: {summary['identity']['observational_change_rate_on_production_trajectory']:.2%}",
            f"- Observational policy-relevant change rate: {summary['identity']['observational_policy_relevant_change_rate']:.2%} of {summary['identity']['observational_policy_relevant_count']}",
            f"- Replacement sources: {summary['identity']['replacement_sources']}",
            f"- Production candidate actions: {summary['identity']['production_candidate_actions']}",
            f"- Production candidates excluded as origin-only: {summary['identity']['production_candidates_origin_only_excluded']}",
            f"- Excluded candidate appearances: {summary['identity']['policy_excluded_candidate_appearances']}",
            "",
            "## Across-run variability",
            "",
            f"- Editorial divergence: {summary['across_run']['editorial_divergence_rate']}",
            f"- Identity divergence: {summary['across_run']['identity_divergence_rate']}",
            f"- Generated share: {summary['across_run']['generated_share']}",
            "",
            "## Interpretation",
            "",
            "These branches consume their own image winners and therefore model counterfactual image-history, spacing, boundary, and cycle consequences. Shared quotes and virtual timestamps isolate image-policy effects. They do not predict human interactions, engagement, future assets, code changes, or X behavior.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Offline production-parity regular quote/image simulator. Never posts or calls network APIs."
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--mode", choices=("observational", "counterfactual"), default="observational")
    parser.add_argument("--quote-coupling", choices=("shared",), default="shared")
    parser.add_argument("--posts-per-run", type=int, default=20)
    parser.add_argument("--seed-base", type=int, default=1000)
    parser.add_argument("--start-time", default="now", help="'now' or ISO-8601 timestamp")
    parser.add_argument("--output-dir", type=Path, help="new private session directory")
    parser.add_argument("--candidate-detail", choices=("none", "top10", "full"), default="top10")
    parser.add_argument("--trace-image", action="append", default=[], metavar="BASENAME", help="record lifecycle reasons for a named image")
    parser.add_argument("--snapshot-dir", type=Path, help="copy an existing immutable input_snapshot")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stop-after-post", type=int, help="checkpoint and stop each run after this post index")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    if args.runs <= 0 or args.posts_per_run <= 0:
        raise SystemExit("--runs and --posts-per-run must be positive")
    if args.stop_after_post is not None and not 1 <= args.stop_after_post <= args.posts_per_run:
        raise SystemExit("--stop-after-post must be between 1 and --posts-per-run")
    start_epoch = parse_start_time(args.start_time)
    session_id = args.output_dir.name if args.output_dir else default_session_id()
    output = args.output_dir or (DEFAULT_OUTPUT_ROOT / session_id)
    session_dir, writer = make_session_directory(output, overwrite=args.overwrite, resume=args.resume)
    started = time.perf_counter()

    if args.resume:
        snapshot = session_dir / "input_snapshot"
        snapshot_manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        session_manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
        if (args.runs, args.posts_per_run, args.seed_base) != (
            session_manifest["runs"],
            session_manifest["posts_per_run"],
            session_manifest["seed_base"],
        ):
            raise ValueError("resume parameters must match existing session manifest")
        if args.mode != session_manifest.get("mode", "observational"):
            raise ValueError("resume mode must match existing session manifest")
        start_epoch = int(session_manifest["start_epoch"])
        args.candidate_detail = str(session_manifest["candidate_detail"])
        args.trace_image = list(session_manifest.get("trace_images", []))
    else:
        if args.snapshot_dir:
            snapshot, snapshot_manifest = copy_existing_snapshot(args.snapshot_dir, session_dir, writer)
        else:
            snapshot, snapshot_manifest = snapshot_inputs(session_dir, writer)
        session_manifest = {
            "schema_version": 1,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "runs": args.runs,
            "posts_per_run": args.posts_per_run,
            "seed_base": args.seed_base,
            "start_epoch": start_epoch,
            "start_local_time": datetime.fromtimestamp(start_epoch).astimezone().isoformat(),
            "candidate_detail": args.candidate_detail,
            "mode": args.mode,
            "quote_coupling": args.quote_coupling,
            "trace_images": sorted(set(args.trace_image)),
            "snapshot_manifest_sha256": sha256_file(snapshot / "manifest.json"),
            "production_source_sha256": sha256_file(ROOT / "mrsMThatcher2.py"),
            "simulator_source_sha256": sha256_file(Path(__file__)),
            "network_allowed": False,
            "xai_calls_allowed": False,
        }
        if args.mode == "counterfactual":
            session_manifest["branch_rng_design"] = {
                "production": "run seed and production RNG consumption unchanged",
                "editorial": "SHA-256-derived independent stream from run seed and branch name",
                "identity": "SHA-256-derived independent stream from run seed and branch name",
                "schedule": "production branch supplies shared quote and virtual-time delays",
            }
        writer.atomic_json(session_dir / "session_manifest.json", session_manifest)

    bot = import_production_bot(session_dir)
    apply_snapshot_config(bot, snapshot)
    session_manifest["effective_config"] = {
        key: getattr(bot, key)
        for key in (
            "POST_SLEEP_MIN",
            "POST_SLEEP_MAX",
            "ENABLE_GENERATED_IMAGE_POOL",
            "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
            "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
            "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING",
            "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
            "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
            "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING",
            "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
            "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
        )
    }
    writer.atomic_json(session_dir / "session_manifest.json", session_manifest)

    with block_process_network():
        for run_index in range(args.runs):
            if args.mode == "observational":
                run_future(
                    bot,
                    writer,
                    session_dir,
                    snapshot,
                    session_id,
                    run_index,
                    args.seed_base + run_index,
                    args.posts_per_run,
                    start_epoch,
                    args.candidate_detail,
                    args.resume,
                    tuple(sorted(set(args.trace_image or session_manifest.get("trace_images", [])))),
                    args.stop_after_post,
                )
            else:
                run_counterfactual_future(
                    bot,
                    writer,
                    session_dir,
                    snapshot,
                    session_id,
                    run_index,
                    args.seed_base + run_index,
                    args.posts_per_run,
                    start_epoch,
                    args.candidate_detail,
                    args.resume,
                    stop_after_post=args.stop_after_post,
                )

    runtime = float(session_manifest.get("accumulated_runtime_seconds", 0.0))
    runtime += time.perf_counter() - started
    session_manifest["accumulated_runtime_seconds"] = runtime
    writer.atomic_json(session_dir / "session_manifest.json", session_manifest)
    if args.mode == "observational":
        records = load_all_records(session_dir)
        summary = summarize_records(records, runtime)
        writer.atomic_json(session_dir / "simulation_summary.json", summary)
        writer.write_text(session_dir / "simulation_report.md", markdown_report(session_manifest, summary))
        count = len(records)
        rate = summary["selections_per_second"]
    else:
        branch_records, comparisons = load_counterfactual_records(session_dir)
        summary = summarize_counterfactual(branch_records, comparisons, runtime)
        writer.atomic_json(session_dir / "counterfactual_simulation_summary.json", summary)
        writer.write_text(
            session_dir / "counterfactual_simulation_report.md",
            counterfactual_markdown_report(session_manifest, summary),
        )
        count = summary["total_branch_selections"]
        rate = summary["branch_selections_per_second"]
    print(f"Simulation complete: {count} selections in {runtime:.3f}s ({rate:.2f}/s)")
    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
