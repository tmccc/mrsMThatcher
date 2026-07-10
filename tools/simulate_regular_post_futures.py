#!/usr/bin/env python3
"""Accelerated offline regular quote/image selection simulator.

The simulator reads one consistent snapshot of production inputs, then runs the
real production selectors against private state. It never calls posting, upload,
reply, lock, X, or xAI functions. All mutable writes are confined to one marked
simulation session directory.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib
import json
import logging
import math
import os
import pickle
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
    pass


class SelectionCaptureError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_same_or_child(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    return resolved == parent_resolved or parent_resolved in resolved.parents


class PrivateWriter:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir.resolve()

    def check(self, path: Path) -> Path:
        resolved = path.resolve()
        if not is_same_or_child(resolved, self.session_dir):
            raise SimulationSafetyError(f"simulation write escaped private session: {resolved}")
        return resolved

    def mkdir(self, path: Path) -> Path:
        checked = self.check(path)
        checked.mkdir(parents=True, exist_ok=True)
        return checked

    def atomic_json(self, path: Path, value: object) -> None:
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
        checked = self.check(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        checked.write_text(text, encoding="utf-8")

    def atomic_text(self, path: Path, text: str) -> None:
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
        checked = self.check(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        with checked.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


def stat_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def read_stable_group_once(paths: Iterable[Path]) -> dict[Path, bytes] | None:
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
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def parse_start_time(value: str) -> int:
    if value.lower() == "now":
        return int(datetime.now().timestamp())
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp())


def default_session_id() -> str:
    return datetime.now().strftime("regular_post_sim_%Y%m%d_%H%M%S")


def validate_session_path(path: Path) -> Path:
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
    bot.LINES_FILE = snapshot / "mrsMThatcher.txt"
    bot.QUOTE_ANALYSIS_FILE = snapshot / "quote_analysis.json"
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
    def fail(*args: object, **kwargs: object) -> Any:
        raise SimulationSafetyError(f"forbidden simulator operation attempted: {name}")

    return fail


def install_hard_guards(bot: Any, writer: PrivateWriter) -> None:
    for name in (
        "upload_media",
        "create_post",
        "ask_grok_for_reply",
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
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def payload(self, prefix: str) -> dict | None:
        for message in reversed(self.messages):
            if message.startswith(prefix):
                return json.loads(message[len(prefix) :])
        return None


@contextmanager
def capture_shadow_selection(bot: Any) -> Iterable[dict]:
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

    def identity_hook(quote: dict, chosen: dict, scored: list[dict], *, selection_phase: str) -> None:
        capture["scored_ids"].append(id(scored))
        bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING = True
        original_identity(quote, chosen, scored, selection_phase=selection_phase)

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


def select_with_production_recovery(bot: Any, lines_used: set[str], images_used: set[str], state: dict) -> dict:
    with capture_shadow_selection(bot) as capture:
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


def rng_state_encode(state: object) -> str:
    return base64.b64encode(pickle.dumps(state, protocol=4)).decode("ascii")


def rng_state_decode(value: str) -> object:
    return pickle.loads(base64.b64decode(value.encode("ascii")))


def load_private_state(snapshot: Path) -> tuple[dict, set[str], set[str]]:
    state = json.loads((snapshot / "bot_state.json").read_text(encoding="utf-8"))
    images_used = {str(item) for item in json.loads((snapshot / "images_used.json").read_text(encoding="utf-8"))}
    lines_used = {str(item) for item in json.loads((snapshot / "lines_used.json").read_text(encoding="utf-8"))}
    return state, images_used, lines_used


def persist_private_state(writer: PrivateWriter, run_dir: Path, state: dict, images_used: set[str], lines_used: set[str]) -> None:
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
) -> int:
    quote = selection["quote"]
    image = selection["image"]

    # Match post_random_quote RNG ordering: schedule delays are sampled only
    # after production has selected the quote/image pair.
    quote_delay = bot.random.randint(bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX)
    meme_delay = None
    if bot.ENABLE_DAILY_MEME_POSTS:
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
    records: list[dict] = []
    for path in sorted((session_dir / "runs").glob("run_*/selections.jsonl")):
        with path.open(encoding="utf-8") as handle:
            records.extend(json.loads(line) for line in handle if line.strip())
    return records


def entropy(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counter.values())


def variation(values: list[float]) -> dict:
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def concentration(counter: Counter[str], denominator: int | None = None) -> dict:
    total = sum(counter.values()) if denominator is None else denominator
    return {
        "observations": total,
        "entropy_bits": entropy(counter),
        "top10_share": sum(count for _, count in counter.most_common(10)) / total if total else 0.0,
        "most_frequent": counter.most_common(15),
    }


def summarize_records(records: list[dict], runtime_seconds: float) -> dict:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline production-parity regular quote/image simulator. Never posts or calls network APIs."
    )
    parser.add_argument("--runs", type=int, default=1)
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
            "trace_images": sorted(set(args.trace_image)),
            "snapshot_manifest_sha256": sha256_file(snapshot / "manifest.json"),
            "production_source_sha256": sha256_file(ROOT / "mrsMThatcher2.py"),
            "simulator_source_sha256": sha256_file(Path(__file__)),
            "network_allowed": False,
            "xai_calls_allowed": False,
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

    records = load_all_records(session_dir)
    runtime = float(session_manifest.get("accumulated_runtime_seconds", 0.0))
    runtime += time.perf_counter() - started
    session_manifest["accumulated_runtime_seconds"] = runtime
    writer.atomic_json(session_dir / "session_manifest.json", session_manifest)
    summary = summarize_records(records, runtime)
    writer.atomic_json(session_dir / "simulation_summary.json", summary)
    writer.write_text(session_dir / "simulation_report.md", markdown_report(session_manifest, summary))
    print(f"Simulation complete: {len(records)} selections in {runtime:.3f}s ({summary['selections_per_second']:.2f}/s)")
    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
