"""Structured pytest evidence for candidate-owned advisory validation.

The plugin is loaded explicitly by :mod:`tools.release_gate`; normal project
pytest runs do not load it.  Under xdist each worker returns its observations
through ``workeroutput`` and the controller writes one deterministic JSON
document at session end. An authoritative external assurance gate must use its
own pinned evidence plugin rather than trusting this candidate-owned module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

try:
    from tools import strict_json
except ModuleNotFoundError:  # Support explicit loading from ``tools/``.
    import strict_json  # type: ignore[no-redef]


EVENTS_PATH_ENV = "MRS_RELEASE_GATE_PYTEST_EVENTS"
IMPORT_POLICY_ENV = "MRS_RELEASE_GATE_IMPORT_POLICY"
CANDIDATE_ROOT_ENV = "MRS_RELEASE_GATE_CANDIDATE_ROOT"


def _state(config: pytest.Config) -> dict[str, list[dict[str, Any]]]:
    state = getattr(config, "_mrs_release_gate_state", None)
    if state is None:
        state = {
            "skips": [],
            "warnings": [],
            "import_violations": [],
            "sys_path_violations": [],
        }
        setattr(config, "_mrs_release_gate_state", state)
    return state


def _candidate_root() -> Path | None:
    raw = os.environ.get(CANDIDATE_ROOT_ENV, "")
    if not raw:
        return None
    try:
        return Path(raw).resolve(strict=True)
    except OSError:
        return None


def _normalise_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    candidate = _candidate_root()
    if candidate is not None:
        text = text.replace(str(candidate), "<candidate>")
    text = re.sub(r"0x[0-9a-fA-F]+", "0x<address>", text)
    return " ".join(text.split())


def _normalise_location(
    location: tuple[str, int, str] | None,
) -> dict[str, object] | None:
    if not location:
        return None
    filename, line_number, function = location
    candidate = _candidate_root()
    path = Path(str(filename))
    if candidate is not None:
        try:
            filename = path.resolve().relative_to(candidate).as_posix()
        except (OSError, ValueError):
            filename = str(path)
    return {
        "path": filename,
        "line": int(line_number),
        "function": str(function or ""),
    }


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = longrepr[2]
    else:
        reason = longrepr
    if getattr(report, "wasxfail", None):
        return _normalise_text(f"xfail: {report.wasxfail}")
    normalised = _normalise_text(reason)
    if normalised.startswith("Skipped: "):
        normalised = normalised[len("Skipped: ") :]
    return normalised


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Capture one exact skip outcome on the controller."""
    if not report.skipped:
        return
    config = getattr(report, "config", None)
    if config is None:
        # pytest reports do not normally retain config; the hook implementation
        # receives the active config through the module-level singleton below.
        config = _ACTIVE_CONFIG
    if config is None:
        return
    if hasattr(config, "workerinput"):
        # xdist replays worker reports to the controller. Recording them in
        # both places would double every remote skip.
        return
    _state(config)["skips"].append(
        {
            "node_id": str(report.nodeid),
            "phase": str(report.when),
            "reason": _skip_reason(report),
            "expected_xfail": bool(getattr(report, "wasxfail", None)),
        }
    )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Capture a collection-time skip which has no runtest report."""
    if not report.skipped or _ACTIVE_CONFIG is None:
        return
    if hasattr(_ACTIVE_CONFIG, "workerinput"):
        return
    _state(_ACTIVE_CONFIG)["skips"].append(
        {
            "node_id": str(report.nodeid),
            "phase": "collection",
            "reason": _normalise_text(report.longrepr),
            "expected_xfail": False,
        }
    )


_ACTIVE_CONFIG: pytest.Config | None = None


def pytest_configure(config: pytest.Config) -> None:
    """Initialise process-local evidence collection."""
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config
    _state(config)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Drop the process-local config reference."""
    global _ACTIVE_CONFIG
    if _ACTIVE_CONFIG is config:
        _ACTIVE_CONFIG = None


def pytest_warning_recorded(
    warning_message: Any,
    when: str,
    nodeid: str,
    location: tuple[str, int, str] | None,
) -> None:
    """Capture stable warning identity without parsing terminal prose."""
    if _ACTIVE_CONFIG is None:
        return
    if hasattr(_ACTIVE_CONFIG, "workerinput"):
        # xdist replays warning hooks to the controller.
        return
    category = getattr(warning_message, "category", Warning)
    category_name = (
        f"{getattr(category, '__module__', '')}."
        f"{getattr(category, '__qualname__', getattr(category, '__name__', 'Warning'))}"
    ).lstrip(".")
    message = _normalise_text(getattr(warning_message, "message", warning_message))
    _state(_ACTIVE_CONFIG)["warnings"].append(
        {
            "category": category_name,
            "message": message,
            "message_fingerprint": hashlib.sha256(
                message.encode("utf-8")
            ).hexdigest(),
            "node_id": str(nodeid or ""),
            "when": str(when or ""),
            "source_location": _normalise_location(location),
        }
    )


_POLICY_CACHE: tuple[str, dict[str, object]] | None = None


def _import_policy() -> dict[str, object]:
    global _POLICY_CACHE
    raw_policy_path = os.environ.get(IMPORT_POLICY_ENV, "")
    if _POLICY_CACHE is not None and _POLICY_CACHE[0] == raw_policy_path:
        return _POLICY_CACHE[1]
    empty: dict[str, object] = {
        "allowed_directory_roots": (),
        "allowed_exact_files": frozenset(),
        "permitted_sys_path_roots": frozenset(),
    }
    try:
        policy_path = Path(raw_policy_path).resolve(strict=True)
        raw = strict_json.load(policy_path)
    except (OSError, strict_json.StrictJSONError):
        _POLICY_CACHE = (raw_policy_path, empty)
        return empty
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        _POLICY_CACHE = (raw_policy_path, empty)
        return empty

    def paths(field: str) -> tuple[Path, ...]:
        values = raw.get(field, [])
        if not isinstance(values, list):
            return ()
        output: list[Path] = []
        for value in values:
            if (
                not isinstance(value, str)
                or not value
                or not os.path.isabs(value)
            ):
                continue
            path = Path(os.path.abspath(value))
            if path not in output:
                output.append(path)
        return tuple(output)

    policy: dict[str, object] = {
        "allowed_directory_roots": paths("allowed_directory_roots"),
        "allowed_exact_files": frozenset(paths("allowed_exact_files")),
        "permitted_sys_path_roots": frozenset(
            paths("permitted_sys_path_roots")
        ),
    }
    _POLICY_CACHE = (raw_policy_path, policy)
    return policy


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _origin_allowed(path: Path, policy: dict[str, object]) -> bool:
    exact_files = policy["allowed_exact_files"]
    directory_roots = policy["allowed_directory_roots"]
    assert isinstance(exact_files, frozenset)
    assert isinstance(directory_roots, tuple)
    return path in exact_files or _within(
        path, directory_roots
    )


def _project_marker(path: Path) -> str | None:
    """Return the first source-project marker above an imported file."""
    for parent in (path.parent, *path.parents):
        for name in (".git", "pyproject.toml", "setup.py", "setup.cfg"):
            try:
                if (parent / name).exists():
                    return str(parent / name)
            except OSError:
                continue
    return None


def _import_violations() -> list[dict[str, str]]:
    policy = _import_policy()
    violations: list[dict[str, str]] = []
    for module_name, module in sorted(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str) or not origin:
            continue
        try:
            path = Path(origin).resolve(strict=True)
        except OSError:
            continue
        if _origin_allowed(path, policy):
            continue
        marker = _project_marker(path)
        violations.append(
            {
                "module": module_name,
                "origin": str(path),
                "project_marker": (
                    marker or "undeclared_distribution_or_project"
                ),
            }
        )
    return violations


def _sys_path_violations() -> list[dict[str, str]]:
    """Return undeclared source-project roots present on active ``sys.path``."""
    policy = _import_policy()
    violations: list[dict[str, str]] = []
    for raw in sorted(set(value for value in sys.path if value)):
        try:
            path = Path(raw).resolve(strict=True)
        except OSError:
            continue
        permitted = policy["permitted_sys_path_roots"]
        assert isinstance(permitted, frozenset)
        if path in permitted:
            continue
        marker = _project_marker(path)
        violations.append(
            {
                "path": str(path),
                "project_marker": marker or "undeclared_sys_path_root",
            }
        )
    return violations


def _payload(config: pytest.Config) -> dict[str, Any]:
    state = _state(config)
    return {
        "schema_version": 1,
        "skips": sorted(
            state["skips"],
            key=lambda row: (
                row["node_id"],
                row["phase"],
                row["reason"],
            ),
        ),
        "warnings": sorted(
            state["warnings"],
            key=lambda row: (
                row["category"],
                row["message_fingerprint"],
                row["node_id"],
                json.dumps(row["source_location"], sort_keys=True),
            ),
        ),
        "import_violations": sorted(
            _import_violations(),
            key=lambda row: (row["module"], row["origin"]),
        ),
        "sys_path_violations": sorted(
            _sys_path_violations(),
            key=lambda row: (row["path"], row["project_marker"]),
        ),
    }


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object | None) -> None:
    """Merge one xdist worker's structured evidence into the controller."""
    del error
    output = getattr(node, "workeroutput", {})
    payload = output.get("mrs_release_gate_events")
    if not isinstance(payload, dict):
        return
    config = node.config
    state = _state(config)
    for key in ("import_violations", "sys_path_violations"):
        rows = payload.get(key, [])
        if isinstance(rows, list):
            state[key].extend(row for row in rows if isinstance(row, dict))


def _write_events(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = strict_json.canonical_json_bytes(payload)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Publish worker data or one controller-owned deterministic event file."""
    del exitstatus
    config = session.config
    payload = _payload(config)
    if hasattr(config, "workerinput"):
        config.workeroutput["mrs_release_gate_events"] = payload
        return
    raw_path = os.environ.get(EVENTS_PATH_ENV, "")
    if not raw_path:
        raise pytest.UsageError(
            f"{EVENTS_PATH_ENV} is required by the release-gate pytest plugin"
        )
    _write_events(Path(raw_path), payload)
