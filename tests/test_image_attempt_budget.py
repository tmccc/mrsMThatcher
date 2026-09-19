"""Offline reservation authority and concurrency regressions for image costs."""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import sys

import pytest

import generate_all_openai_quote_images as images


@pytest.fixture
def image_cli(tmp_path, monkeypatch):
    """Run the real CLI against temporary corpus files with provider I/O forbidden."""
    from tests.helpers.bot_fixtures import quote_analysis_for_lines

    quote = "An offline image generation fixture."
    source = tmp_path / "quotes.txt"
    analysis = tmp_path / "quote_analysis.json"
    source.write_text(quote + "\n")
    analysis.write_text(json.dumps(quote_analysis_for_lines(
        [quote], {0: {"summary": "A self-contained offline illustration brief."}},
    )))
    output = tmp_path / "output"
    monkeypatch.setattr(images, "load_env_file", lambda _path: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden_request(*_args, **_kwargs):
        pytest.fail("CLI regression must not make a provider request")

    monkeypatch.setattr(images.requests.Session, "request", forbidden_request)
    monkeypatch.setattr(images, "request_image", forbidden_request)

    def run(*options):
        monkeypatch.setattr(sys, "argv", [
            "generate_all_openai_quote_images.py",
            "--quote-file", str(source), "--quote-analysis", str(analysis),
            "--out", str(output), "--env-file", str(tmp_path / "unused.env"),
            "--estimated-cost-per-image", "1", "--sleep", "0", *options,
        ])
        return images.main()

    return run, output


def _stub_image_generation(monkeypatch):
    """Exercise the real reservation callback without contacting a provider."""
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "offline-placeholder")

    def request(_session, *, reserve_attempt, **_kwargs):
        reserve_attempt()
        calls.append("reserved")
        return {"data": [{"b64_json": "b2ZmbGluZSBmaXh0dXJl"}]}

    monkeypatch.setattr(images, "request_image", request)
    return calls


def test_main_creates_private_output_under_default_umask(image_cli, monkeypatch):
    run, output = image_cli
    calls = _stub_image_generation(monkeypatch)
    previous = os.umask(0o002)
    try:
        assert run() == 0
    finally:
        os.umask(previous)
    assert output.stat().st_mode & 0o777 == 0o700
    assert calls == ["reserved"]
    ledger = json.loads((output / "attempt_cost_reservations.json").read_text())
    assert ledger["attempted_requests"] == 1
    assert json.loads((output / "run_manifest.json").read_text())["generated_this_run"] == 1


def test_main_rejects_existing_unsafe_output_directory(image_cli, monkeypatch):
    run, output = image_cli
    calls = _stub_image_generation(monkeypatch)
    output.mkdir()
    output.chmod(0o775)
    with pytest.raises(RuntimeError, match="unsafe"):
        run()
    assert output.stat().st_mode & 0o777 == 0o775
    assert calls == []
    assert not list(output.glob("attempt_cost_reservations.json*"))


def test_main_dry_run_does_not_create_or_pin_spending_authority(image_cli, monkeypatch):
    run, output = image_cli
    assert run("--dry-run", "--max-estimated-cost", "0") == 0
    assert not list(output.glob("attempt_cost_reservations.json*"))
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["summary"]["dry_run"] == 1
    assert list(output.glob("items/*/generation_prompt.txt"))

    calls = _stub_image_generation(monkeypatch)
    assert run("--max-estimated-cost", "12") == 0
    assert calls == ["reserved"]
    ledger = json.loads((output / "attempt_cost_reservations.json").read_text())
    assert ledger == {
        "attempted_requests": 1,
        "reserved_estimated_cost_usd": "1.0",
        "max_estimated_cost_usd": "12.0",
    }


@pytest.mark.parametrize("preview_cost", ["1", "2"])
def test_main_dry_run_preserves_existing_budget_bytes_and_identity(image_cli, preview_cost):
    run, output = image_cli
    output.mkdir(mode=0o700)
    path = output / "attempt_cost_reservations.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=12)
    budget.reserve()
    authority = [path, path.with_name(path.name + ".lock")]
    before = [(entry.read_bytes(), entry.stat()) for entry in authority]
    assert run("--dry-run", "--max-estimated-cost", "0",
               "--estimated-cost-per-image", preview_cost) == 0
    for entry, (data, metadata) in zip(authority, before):
        current = entry.stat()
        assert entry.read_bytes() == data
        assert (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_ctime_ns) == (
            metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns,
        )


def test_preexisting_budget_instances_cannot_each_reserve_the_first_request(tmp_path):
    path = tmp_path / "exposure.json"
    first = images.AttemptBudget(path, per_request=1, ceiling=1)
    stale = images.AttemptBudget(path, per_request=1, ceiling=1)
    first.reserve()
    with pytest.raises(RuntimeError, match="ceiling"):
        stale.reserve()
    assert json.loads(path.read_text())["attempted_requests"] == 1


def _reserve_worker(path, start, results):
    budget = images.AttemptBudget(Path(path), per_request=1, ceiling=1)
    results.put("ready")
    start.wait(5)
    try:
        budget.reserve()
    except RuntimeError:
        results.put("blocked")
    else:
        results.put("reserved")


def test_separate_processes_share_one_atomic_budget(tmp_path):
    path = tmp_path / "exposure.json"
    images.AttemptBudget(path, per_request=1, ceiling=1)
    context = multiprocessing.get_context("fork")
    start, results = context.Event(), context.Queue()
    children = [context.Process(target=_reserve_worker, args=(str(path), start, results)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        assert [results.get(timeout=10) for _ in children] == ["ready", "ready"]
        start.set()
        assert sorted(results.get(timeout=10) for _ in children) == ["blocked", "reserved"]
        for child in children:
            child.join(10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        results.close()
    assert json.loads(path.read_text())["attempted_requests"] == 1


@pytest.mark.parametrize("target", ["ledger", "lock"])
@pytest.mark.parametrize("attack", ["symlink", "hardlink", "writable", "fifo"])
def test_budget_namespace_authority_is_exact_and_private(tmp_path, target, attack):
    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=2)
    attacked = path if target == "ledger" else path.with_name(path.name + ".lock")
    saved = attacked.read_bytes()
    if attack == "symlink":
        destination = tmp_path / "target.json"
        attacked.rename(destination)
        attacked.symlink_to(destination)
    elif attack == "hardlink":
        os.link(attacked, tmp_path / "second-link")
    elif attack == "writable":
        attacked.chmod(0o660)
    else:
        attacked.unlink()
        os.mkfifo(attacked, mode=0o600)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        budget.reserve()
    assert budget.attempts == 0
    if attack != "fifo":
        assert attacked.read_bytes() == saved


def test_budget_rejects_unsafe_parent_and_missing_persisted_ledger(tmp_path):
    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=2)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_name(path.name + ".lock").stat().st_mode & 0o777 == 0o600
    tmp_path.chmod(0o770)
    try:
        with pytest.raises(RuntimeError, match="unsafe"):
            budget.reserve()
    finally:
        tmp_path.chmod(0o700)
    path.unlink()
    with pytest.raises(RuntimeError, match="disappeared"):
        budget.reserve()
    with pytest.raises(RuntimeError, match="missing"):
        images.AttemptBudget(path, per_request=1, ceiling=2)


def test_lock_namespace_substitution_after_acquiring_flock_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=2)
    lock = path.with_name(path.name + ".lock")
    flock = images.fcntl.flock

    def substitute(descriptor, operation):
        flock(descriptor, operation)
        lock.rename(tmp_path / "old-lock")
        lock.write_bytes(b"")

    monkeypatch.setattr(images.fcntl, "flock", substitute)
    with pytest.raises(RuntimeError, match="lock identity changed"):
        budget.reserve()
    assert json.loads(path.read_text())["attempted_requests"] == 0


def test_ledger_substitution_during_staging_prevents_publication(monkeypatch, tmp_path):
    import exact_receipt_retirement as durable

    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=2)
    initial = path.read_bytes()
    stage = durable._stage_new

    def substitute(*args, **kwargs):
        entry = stage(*args, **kwargs)
        alternate = tmp_path / "alternate"
        alternate.write_bytes(initial)
        alternate.replace(path)
        return entry

    monkeypatch.setattr(durable, "_stage_new", substitute)
    with pytest.raises(RuntimeError, match="changed before reservation"):
        budget.reserve()
    assert path.read_bytes() == initial
    assert list(tmp_path.glob(".*.tmp")) == []


def test_failed_reservation_never_calls_provider_and_committed_failure_remains_counted(monkeypatch, tmp_path):
    from types import SimpleNamespace

    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=1)
    replace, fsync = images.os.replace, images.os.fsync
    committed = False
    calls = []

    def replace_then_mark(*args, **kwargs):
        nonlocal committed
        result = replace(*args, **kwargs)
        committed = True
        return result

    def fail_directory_sync(descriptor):
        if committed:
            raise OSError("post-replacement directory fsync failed")
        return fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(images.os, "replace", replace_then_mark)
        patch.setattr(images.os, "fsync", fail_directory_sync)
        with pytest.raises(OSError, match="fsync"):
            images.request_image(SimpleNamespace(post=lambda *a, **kw: calls.append(kw)),
                api_key="test", model="test", prompt="test", quality="low", size="1024x1024",
                max_retries=3, reserve_attempt=budget.reserve)
    assert calls == []
    recovered = images.AttemptBudget(path, per_request=1, ceiling=1)
    assert recovered.attempts == 1
    with pytest.raises(RuntimeError, match="ceiling"):
        recovered.reserve()


def test_reservation_failure_before_replacement_preserves_count_and_cleans_staging(monkeypatch, tmp_path):
    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=1)
    prior = path.read_bytes()
    with monkeypatch.context() as patch:
        def reject(*args, **kwargs):
            raise OSError("before replacement")
        patch.setattr(images.os, "replace", reject)
        with pytest.raises(OSError, match="before replacement"):
            budget.reserve()
    assert path.read_bytes() == prior
    assert list(tmp_path.glob(".*.tmp")) == []
    budget.reserve()
    assert budget.attempts == 1


def test_recovered_budget_cannot_silently_increase_persisted_ceiling(tmp_path):
    path = tmp_path / "exposure.json"
    first = images.AttemptBudget(path, per_request=0.1, ceiling=0.2)
    first.reserve()
    recovered = images.AttemptBudget(path, per_request=0.1, ceiling=100)
    recovered.reserve()
    with pytest.raises(RuntimeError, match="ceiling"):
        recovered.reserve()
    with pytest.raises(ValueError, match="cost estimate changed"):
        images.AttemptBudget(path, per_request=0.2, ceiling=100)


def test_identical_bytes_substituted_after_replace_do_not_prove_the_fsynced_commit(monkeypatch, tmp_path):
    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=2)
    replace = images.os.replace

    def substitute_after_replace(source, target, **kwargs):
        result = replace(source, target, **kwargs)
        alternate = tmp_path / "alternate"
        alternate.write_bytes(path.read_bytes())
        replace(alternate, path)
        return result

    monkeypatch.setattr(images.os, "replace", substitute_after_replace)
    with pytest.raises(RuntimeError, match="changed during publication"):
        budget.reserve()
    assert budget.attempts == 0


def test_fresh_python_process_recovers_reserved_cost_without_an_api_call(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "exposure.json"
    budget = images.AttemptBudget(path, per_request=1, ceiling=1)
    budget.reserve()
    code = """
import sys
from pathlib import Path
from generate_all_openai_quote_images import AttemptBudget
budget = AttemptBudget(Path(sys.argv[1]), per_request=1, ceiling=1)
assert budget.attempts == 1
try:
    budget.reserve()
except RuntimeError as error:
    assert 'ceiling' in str(error)
else:
    raise AssertionError('fresh process lost reserved exposure')
"""
    result = subprocess.run([sys.executable, "-c", code, str(path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
