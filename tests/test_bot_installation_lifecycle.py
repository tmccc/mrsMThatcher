from __future__ import annotations

import builtins
from contextlib import nullcontext
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.installation_fixtures import install_paths
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401

DEPENDENCIES = {'required_installation_files_missing': ['HISTORICAL_CONTEXT_REPLY_HISTORY_FILE',
                                         'HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE',
                                         'IMAGES_USED_FILE',
                                         'INSTALLATION_IN_PROGRESS_FILE',
                                         'LINES_USED_FILE',
                                         'STATE_BACKUP_COUNT',
                                         'STATE_FILE',
                                         'durable_state_namespace_is_owned_single_link_file',
                                         'os',
                                         'remote_source_receipt_paths',
                                         'retirement_ledger_is_blocking',
                                         'retirement_ledger_paths'],
 'require_established_installation': ['required_installation_files_missing'],
 'recover_interrupted_retirement_ledger_exchanges_at_startup': ['ProtocolActivationError',
                                                                'REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE',
                                                                'inspect_protocol_activation',
                                                                'inspect_retirement_ledger',
                                                                'recover_retirement_ledger_exchange_if_present',
                                                                'remote_source_receipt_paths',
                                                                'transaction_mutation_authority'],
 'require_established_installation_after_ledger_recovery': ['log',
                                                            'recover_interrupted_retirement_ledger_exchanges_at_startup',
                                                            'require_established_installation'],
 'initialise_installation': ['AMBIGUOUS_POST_OUTCOME_FILE',
                             'AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE',
                             'BASE_DIR',
                             'CONFIRMED_REPLY_RECEIPT_FILE',
                             'ENABLE_DAILY_MEME_POSTS',
                             'HISTORICAL_CONTEXT_REPLY_HISTORY_FILE',
                             'HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE',
                             'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                             'IMAGES_USED_FILE',
                             'INSTALLATION_IN_PROGRESS_FILE',
                             'INSTALLATION_MARKER_FILE',
                             'JOURNAL_RETIREMENT_PREFIX',
                             'JOURNAL_STAGING_PREFIX',
                             'LINES_USED_FILE',
                             'MEDIA_RETIREMENT_GUARD_PREFIX',
                             'MEDIA_TRANSITION_PREFIX',
                             'MEDIA_UPLOAD_RECEIPT_FILE',
                             'MEME_POST_RECEIPT_FILE',
                             'POST_SLEEP_MIN',
                             'REGULAR_POST_RECEIPT_FILE',
                             'REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME',
                             'REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE',
                             'STATE_BACKUP_COUNT',
                             'STATE_FILE',
                             'acquire_instance_lock',
                             'atomic_write_json',
                             'default_state',
                             'ensure_meme_schedule_initialized',
                             'fence_path_for_journal',
                             'fsync_parent_dir',
                             'historical_context_outbox_store',
                             'historical_context_reply_store',
                             'initialise_retirement_ledger',
                             'journal_path_for_receipt',
                             'log',
                             'media_fence_path_for_receipt',
                             'now_epoch',
                             'os',
                             'remote_source_receipt_paths',
                             'require_production_bootstrap',
                             'retirement_auxiliary_paths',
                             'retirement_ledger_paths',
                             'save_image_used_basenames',
                             'save_quote_used_hashes',
                             'save_state',
                             'transaction_mutation_authority']}
SIGNATURES = {'required_installation_files_missing': "() -> 'list[Path]'",
 'require_established_installation': "() -> 'None'",
 'recover_interrupted_retirement_ledger_exchanges_at_startup': '() -> '
                                                               "'tuple[Path, "
                                                               "...]'",
 'require_established_installation_after_ledger_recovery': "() -> 'None'",
 'initialise_installation': "() -> 'int'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Installation lifecycle import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority'} or name.startswith('mrs_bot_') and name != 'mrs_bot_installation_lifecycle':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_installation_lifecycle
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_installation_lifecycle.required_installation_files_missing.__annotations__['return'] == 'list[Path]'
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", DEPENDENCIES)
def test_adapters_preserve_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {dep: object() for dep in DEPENDENCIES[name]}
            for dep, value in current.items():
                patch.setattr(bot, dep, value)
            result = {"original": []}
            expected = {}

            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only} | current
                assert kwargs.keys() == supplied.keys()
                assert all(kwargs[key] is value for key, value in supplied.items())
                return result

            patch.setattr(bot, "_installation_lifecycle", SimpleNamespace(**{name: capture}))
            for include_defaults in (True, False):
                provided = {key: object() for key, param in signature.parameters.items()
                            if include_defaults or param.default is inspect.Parameter.empty}
                bound = signature.bind(**provided)
                bound.apply_defaults()
                expected = bound.arguments
                assert adapter(**provided) is result
            with pytest.raises(TypeError, match="not_a_public_option"):
                adapter(**provided, not_a_public_option={})
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_installation_lifecycle", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.mark.parametrize("sentinel", ["missing", "present", "unreadable"])
def test_establishment_keeps_limits_repeated_probes_order_and_references(monkeypatch, tmp_path, sentinel):
    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    trace = Mock()
    used = bot.LINES_USED_FILE
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", used)
    state = bot.STATE_FILE
    backup1, backup2 = (state.with_name(f"{state.name}.bak{i}") for i in (1, 2))
    receipt1, receipt2, ledger2 = (tmp_path / name for name in ("r1", "r2", "ledger2"))
    duplicate_used = Path(str(used))
    assert duplicate_used is not used
    trace.receipts.return_value = (receipt1, receipt2)
    trace.blocking.return_value = True
    trace.ledgers.side_effect = [(duplicate_used, object()), (ledger2, object())]
    trace.namespace.side_effect = [False, False, True, True, False, False, True]

    def lstat(path):
        if path == bot.INSTALLATION_IN_PROGRESS_FILE:
            if sentinel == "missing":
                raise FileNotFoundError
            if sentinel == "unreadable":
                raise PermissionError
        elif path == state:
            raise FileNotFoundError
        elif path == backup2:
            raise OSError

    trace.lstat.side_effect = lstat
    for name, callback in {
        "remote_source_receipt_paths": trace.receipts,
        "retirement_ledger_is_blocking": trace.blocking,
        "retirement_ledger_paths": trace.ledgers,
        "durable_state_namespace_is_owned_single_link_file": trace.namespace,
    }.items():
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=trace.lstat))
    result = bot.required_installation_files_missing()
    expected = [used, ledger2]
    if sentinel != "missing":
        expected.append(bot.INSTALLATION_IN_PROGRESS_FILE)
    expected += [backup1, backup2]
    assert result == expected
    assert result[0] is used and result[1] is ledger2
    assert trace.mock_calls == [
        call.namespace(used), call.namespace(used),
        call.namespace(bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE, maximum_bytes=None),
        call.namespace(bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE, maximum_bytes=None),
        call.receipts(), call.blocking(receipt1), call.ledgers(receipt1),
        call.blocking(receipt2), call.ledgers(receipt2),
        call.lstat(bot.INSTALLATION_IN_PROGRESS_FILE),
        call.lstat(state), call.lstat(backup1), call.namespace(backup1),
        call.lstat(backup2), call.namespace(state), call.namespace(backup1),
    ]


def test_establishment_without_any_state_candidate_appends_original_state(monkeypatch, tmp_path):
    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "remote_source_receipt_paths", lambda: ())
    namespace = Mock(side_effect=[True, True, True, True, False])
    monkeypatch.setattr(bot, "durable_state_namespace_is_owned_single_link_file", namespace)
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=Mock(side_effect=FileNotFoundError)))
    missing = bot.required_installation_files_missing()
    assert len(missing) == 1 and missing[0] is bot.STATE_FILE
    assert namespace.call_args_list[-1] == call(bot.STATE_FILE)


@pytest.mark.parametrize("missing", [[], [Path("second"), Path("first")]])
def test_establishment_uses_current_sibling_and_exact_ordered_message(monkeypatch, missing):
    sibling = Mock(return_value=missing)
    monkeypatch.setattr(bot, "required_installation_files_missing", sibling)
    if missing:
        with pytest.raises(RuntimeError) as caught:
            bot.require_established_installation()
        assert str(caught.value) == (
            "Required durable production state/history is missing: second, first. "
            "Restore the files or use --initialise only for a genuinely new installation."
        )
    else:
        assert bot.require_established_installation() is None
    sibling.assert_called_once_with()
    failure = OSError("current missing-file callback")
    sibling.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.require_established_installation()
    assert caught.value is failure


def _recovery_trace(monkeypatch, tmp_path):
    trace = Mock()
    paths = tuple(tmp_path / name for name in ("first", "second", "third"))
    trace.receipts.return_value = paths
    trace.inspect.return_value = SimpleNamespace(valid=True, blocking=True, state="exchange_staged")
    trace.authority.return_value = object()
    trace.recover.return_value = True
    for name, callback in {
        "remote_source_receipt_paths": trace.receipts,
        "inspect_retirement_ledger": trace.inspect,
        "inspect_protocol_activation": trace.activation,
        "transaction_mutation_authority": trace.authority,
        "recover_retirement_ledger_exchange_if_present": trace.recover,
    }.items():
        monkeypatch.setattr(bot, name, callback)
    return trace, paths


def test_recovery_eagerly_inspects_then_recovers_in_insertion_order_with_one_authority(monkeypatch, tmp_path):
    trace, (first, second, third) = _recovery_trace(monkeypatch, tmp_path)
    trace.receipts.return_value = (first, second, third, first)
    trace.inspect.side_effect = [
        SimpleNamespace(valid=False),
        SimpleNamespace(valid=True, blocking=True, state="exchange_committed"),
        SimpleNamespace(valid=True, blocking=False),
        SimpleNamespace(valid=True, blocking=True, state="exchange_staged"),
    ]
    trace.recover.side_effect = [False, object()]
    result = bot.recover_interrupted_retirement_ledger_exchanges_at_startup()
    assert len(result) == 1 and result[0] is second
    authority = trace.authority.return_value
    assert trace.mock_calls == [
        call.receipts(), call.inspect(first), call.inspect(second),
        call.inspect(third), call.inspect(first),
        call.activation(bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE),
        call.authority("startup retirement-ledger atomic exchange recovery"),
        call.recover(first, mutation_authority=authority),
        call.recover(second, mutation_authority=authority),
    ]
    assert all(item.kwargs["mutation_authority"] is authority for item in trace.recover.call_args_list)


@pytest.mark.parametrize("inspection", [
    SimpleNamespace(valid=False),
    SimpleNamespace(valid=True, blocking=False),
    SimpleNamespace(valid=True, blocking=True, state="idle"),
    SimpleNamespace(valid=True, blocking=True, state="completed"),
])
def test_nonrecoverable_inspections_short_circuit_without_activation(monkeypatch, tmp_path, inspection):
    trace, paths = _recovery_trace(monkeypatch, tmp_path)
    trace.inspect.return_value = inspection
    assert bot.recover_interrupted_retirement_ledger_exchanges_at_startup() == ()
    assert trace.mock_calls == [call.receipts(), *(call.inspect(path) for path in paths)]


@pytest.mark.parametrize("step, error_type, swallowed", [
    ("activation", bot.ProtocolActivationError, True),
    ("activation", OSError, True),
    ("activation", TypeError, False),
    ("inspect", OSError, False),
    ("authority", bot.ProtocolActivationError, False),
    ("authority", OSError, False),
    ("recover", bot.ProtocolActivationError, False),
    ("recover", OSError, False),
])
def test_recovery_catches_only_activation_inspection_failures(monkeypatch, tmp_path, step, error_type, swallowed):
    trace, paths = _recovery_trace(monkeypatch, tmp_path)
    failure = error_type("injected current dependency failure")
    getattr(trace, step).side_effect = failure
    if swallowed:
        assert bot.recover_interrupted_retirement_ledger_exchanges_at_startup() == ()
    else:
        with pytest.raises(error_type) as caught:
            bot.recover_interrupted_retirement_ledger_exchanges_at_startup()
        assert caught.value is failure
    if step == "inspect":
        assert trace.mock_calls == [call.receipts(), call.inspect(paths[0])]
    if step in {"inspect", "activation"}:
        trace.authority.assert_not_called()
    if step != "recover":
        trace.recover.assert_not_called()
    else:
        trace.recover.assert_called_once_with(paths[0], mutation_authority=trace.authority.return_value)


@pytest.mark.parametrize("step", [None, "recovery", "warning", "establishment", "empty"])
def test_recovery_wrapper_warning_precedes_current_establishment_and_keeps_failures(monkeypatch, step):
    trace = Mock()
    trace.recovery.return_value = () if step == "empty" else (Path("second"), Path("first"))
    failure = OSError("wrapper boundary")
    if step not in {None, "empty"}:
        getattr(trace, step).side_effect = failure
    monkeypatch.setattr(bot, "recover_interrupted_retirement_ledger_exchanges_at_startup", trace.recovery)
    monkeypatch.setattr(bot, "require_established_installation", trace.establishment)
    monkeypatch.setattr(bot, "log", SimpleNamespace(warning=trace.warning))
    if step in {None, "empty"}:
        assert bot.require_established_installation_after_ledger_recovery() is None
    else:
        with pytest.raises(OSError) as caught:
            bot.require_established_installation_after_ledger_recovery()
        assert caught.value is failure
    expected = [call.recovery()]
    if step not in {"recovery", "empty"}:
        expected.append(call.warning(
            "Recovered crash-left permanent retirement-ledger exchanges before installation validation: %s",
            ["second", "first"],
        ))
    if step not in {"recovery", "warning"}:
        expected.append(call.establishment())
    assert trace.mock_calls == expected


def _initialisation_trace(monkeypatch, tmp_path):
    base = tmp_path / "installation"
    install_paths(monkeypatch, base, activate_protocol=False)
    trace = Mock()
    receipts = (bot.REGULAR_POST_RECEIPT_FILE, bot.MEME_POST_RECEIPT_FILE)
    for name in (
        "require_production_bootstrap", "acquire_instance_lock", "now_epoch",
        "default_state", "ensure_meme_schedule_initialized", "save_state",
        "save_quote_used_hashes", "save_image_used_basenames", "atomic_write_json",
        "historical_context_reply_store", "historical_context_outbox_store",
        "transaction_mutation_authority", "initialise_retirement_ledger",
        "remote_source_receipt_paths", "retirement_auxiliary_paths",
        "retirement_ledger_paths", "journal_path_for_receipt", "fence_path_for_journal",
        "media_fence_path_for_receipt", "fsync_parent_dir",
    ):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    for name, value in {
        "ENABLE_DAILY_MEME_POSTS": True, "POST_SLEEP_MIN": 37,
        "JOURNAL_STAGING_PREFIX": "stage-", "JOURNAL_RETIREMENT_PREFIX": "retire-",
        "MEDIA_TRANSITION_PREFIX": "media-", "MEDIA_RETIREMENT_GUARD_PREFIX": "guard-",
    }.items():
        monkeypatch.setattr(bot, name, value)
    trace.now_epoch.return_value = 100
    trace.default_state.return_value = {"keep": object()}
    trace.remote_source_receipt_paths.return_value = receipts
    trace.retirement_auxiliary_paths.side_effect = lambda path: (base / "shared-aux", path.with_suffix(".aux"))
    trace.retirement_ledger_paths.side_effect = lambda path: (path.with_suffix(".ledger"), path.with_suffix(".exchange"))
    trace.journal_path_for_receipt.return_value = base / "journal"
    trace.fence_path_for_journal.return_value = base / "journal-fence"
    trace.media_fence_path_for_receipt.return_value = base / "media-fence"
    trace.historical_context_reply_store.return_value = SimpleNamespace(
        history_path=bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        initialise_empty_history=trace.history_initialise,
    )
    trace.historical_context_outbox_store.return_value = SimpleNamespace(
        path=bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        lock_path=base / "historical_context_reply_outbox.json.lock",
        initialise_empty=trace.outbox_initialise,
    )
    trace.transaction_mutation_authority.return_value = object()
    trace.initialise_retirement_ledger.return_value = SimpleNamespace(
        valid=True, blocking=False, state="idle", sequence=0,
    )
    trace.scandir.return_value = nullcontext([
        SimpleNamespace(name=name)
        for name in ("ignored", "stage-one", "retire-one", "media-one", "guard-one")
    ])
    trace.lstat.side_effect = FileNotFoundError
    monkeypatch.setattr(bot, "os", SimpleNamespace(scandir=trace.scandir, lstat=trace.lstat))
    monkeypatch.setattr(bot, "log", SimpleNamespace(error=trace.error))
    original_mkdir, original_unlink = Path.mkdir, Path.unlink

    def mkdir(path, *args, **kwargs):
        if path == base:
            return trace.mkdir(path, *args, **kwargs)
        return original_mkdir(path, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        if path.parent == base:
            return trace.unlink(path, *args, **kwargs)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "unlink", unlink)
    original_import = builtins.__import__

    def observed_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "historical_context_formatter" and "HistoricalContextReplyStore" in fromlist:
            trace.formatter_import()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", observed_import)
    return trace, base, receipts


@pytest.mark.parametrize("memes, missing_directory", [(True, False), (False, True)])
def test_initialisation_exact_inventory_write_order_and_state_authority_references(monkeypatch, tmp_path, capsys, memes, missing_directory):
    trace, base, receipts = _initialisation_trace(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", memes)
    if missing_directory:
        trace.scandir.side_effect = FileNotFoundError
    assert bot.initialise_installation() == 0
    candidates = [
        ".mrsMThatcher.initialised.json", ".mrsMThatcher.initialising.json",
        "bot_state.json", "bot_state.tmp", "lines_used.json", "lines_used.json.tmp",
        "images_used.json", "images_used.json.tmp",
        "bot_state.json.bak1", "bot_state.json.bak2",
        "bot_state.json.bak1.tmp", "bot_state.json.bak2.tmp",
        "regular_post_receipt.json", "meme_post_receipt.json", "confirmed_reply_receipt.json",
        "journal", "journal-fence", "remote_media_upload_receipt.json", "media-fence",
        "ambiguous_post_outcome.json", "ambiguous_post_outcome.restart_barrier.json",
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME,
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE.name,
        "historical_context_reply_history.json", "historical_context_reply_receipt.json",
        "historical_context_reply_outbox.json", "historical_context_reply_outbox.json.lock",
        "historical_context_reply_outbox.json.worker.lock",
        "shared-aux", "regular_post_receipt.aux", "shared-aux", "meme_post_receipt.aux",
        "regular_post_receipt.ledger", "regular_post_receipt.exchange",
        "meme_post_receipt.ledger", "meme_post_receipt.exchange",
    ]
    if not missing_directory:
        candidates += ["stage-one", "retire-one", "media-one", "guard-one"]
    expected = [
        call.formatter_import(), call.require_production_bootstrap(), call.acquire_instance_lock(),
        call.journal_path_for_receipt(receipts[0]), call.journal_path_for_receipt(receipts[0]),
        call.fence_path_for_journal(base / "journal"),
        call.media_fence_path_for_receipt(bot.MEDIA_UPLOAD_RECEIPT_FILE),
        call.remote_source_receipt_paths(),
        *(call.retirement_auxiliary_paths(path) for path in receipts),
        call.remote_source_receipt_paths(),
        *(call.retirement_ledger_paths(path) for path in receipts),
        call.scandir(base), *(call.lstat(base / name) for name in candidates),
        call.mkdir(base, parents=True, exist_ok=True), call.now_epoch(),
        call.atomic_write_json(bot.INSTALLATION_IN_PROGRESS_FILE, {
            "schema_version": 1, "state": "initialising", "started_at_epoch": 100,
        }, durable=True),
        call.default_state(),
    ]
    state = trace.default_state.return_value
    assert state["next_quote_post_epoch"] == 137
    if memes:
        expected.append(call.ensure_meme_schedule_initialized(state))
        assert trace.ensure_meme_schedule_initialized.call_args.args[0] is state
    expected += [
        call.save_state(state, durable=True),
        call.save_quote_used_hashes(bot.LINES_USED_FILE, set(), durable=True),
        call.save_image_used_basenames(bot.IMAGES_USED_FILE, set(), durable=True),
        call.historical_context_reply_store(allow_missing_history=True), call.history_initialise(),
        call.historical_context_outbox_store(), call.outbox_initialise(),
        call.transaction_mutation_authority("new-install retirement-ledger initialisation"),
        call.remote_source_receipt_paths(),
    ]
    authority = trace.transaction_mutation_authority.return_value
    for path in receipts:
        expected += [call.retirement_ledger_paths(path), call.initialise_retirement_ledger(path, mutation_authority=authority)]
    expected += [
        call.atomic_write_json(bot.INSTALLATION_MARKER_FILE, {
            "schema_version": 1, "initialised_at_epoch": 100,
        }, durable=True),
        call.unlink(bot.INSTALLATION_IN_PROGRESS_FILE),
        call.fsync_parent_dir(bot.INSTALLATION_IN_PROGRESS_FILE, strict=True),
    ]
    assert trace.mock_calls == expected
    assert trace.save_state.call_args.args[0] is state
    assert trace.save_quote_used_hashes.call_args.args[1] is not trace.save_image_used_basenames.call_args.args[1]
    for item, path in zip(trace.initialise_retirement_ledger.call_args_list, receipts):
        assert item.args[0] is path and item.kwargs["mutation_authority"] is authority
    assert capsys.readouterr().out == (
        f"Initialised durable MrsMThatcher state in {base}; production was not started. "
        "Remote writes remain disabled until the stopped external-attestation protocol activator succeeds.\n"
    )


@pytest.mark.parametrize("step, error_type, wrapped", [
    ("formatter_import", ImportError, False),
    ("require_production_bootstrap", OSError, False),
    ("acquire_instance_lock", OSError, False),
    ("scandir", OSError, True), ("scandir", TypeError, False),
    ("lstat", OSError, True), ("lstat", TypeError, False),
    ("mkdir", OSError, False),
])
def test_initialisation_inventory_failure_causes_and_precreation_scope(monkeypatch, tmp_path, step, error_type, wrapped):
    trace, base, _receipts = _initialisation_trace(monkeypatch, tmp_path)
    failure = error_type("inventory boundary")
    getattr(trace, step).side_effect = failure
    with pytest.raises(RuntimeError if wrapped else error_type) as caught:
        bot.initialise_installation()
    if wrapped:
        assert caught.value.__cause__ is failure
        expected = (
            "Refusing to initialise because the state directory namespace cannot be inventoried"
            if step == "scandir" else
            f"Refusing to initialise because an existing-state namespace entry cannot be inspected: {bot.INSTALLATION_MARKER_FILE}"
        )
        assert str(caught.value) == expected
    else:
        assert caught.value is failure
    trace.now_epoch.assert_not_called()
    trace.atomic_write_json.assert_not_called()
    trace.unlink.assert_not_called()
    assert trace.mock_calls[-1][0] == step
    if step in {"formatter_import", "require_production_bootstrap", "acquire_instance_lock"}:
        trace.scandir.assert_not_called()
        trace.lstat.assert_not_called()
    if step != "mkdir":
        trace.mkdir.assert_not_called()


@pytest.mark.parametrize("values", [
    {"valid": False}, {"valid": True, "blocking": True},
    {"valid": True, "blocking": False, "state": "completed"},
    {"valid": True, "blocking": False, "state": "idle", "sequence": 1},
])
def test_initialisation_genesis_checks_short_circuit_with_preregistered_ledger_paths(monkeypatch, tmp_path, values):
    trace, _base, receipts = _initialisation_trace(monkeypatch, tmp_path)
    observed = []

    class Genesis:
        def __getattr__(self, name):
            observed.append(name)
            return values[name]

    trace.initialise_retirement_ledger.return_value = Genesis()
    with pytest.raises(RuntimeError) as caught:
        bot.initialise_installation()
    assert str(caught.value) == f"New-install retirement ledger did not reach its exact genesis state: {receipts[0]}"
    assert observed == list(values)
    trace.initialise_retirement_ledger.assert_called_once_with(
        receipts[0], mutation_authority=trace.transaction_mutation_authority.return_value,
    )
    assert trace.unlink.call_args_list[:2] == [
        call(receipts[0].with_suffix(".exchange")), call(receipts[0].with_suffix(".ledger")),
    ]
    assert trace.atomic_write_json.call_count == 1


@pytest.mark.parametrize("mode", ["original", "logger", "attribute", "native_unlink"])
def test_initialisation_reverse_rollback_diagnostics_and_native_failure_precedence(monkeypatch, tmp_path, mode):
    trace, base, _receipts = _initialisation_trace(monkeypatch, tmp_path)
    attribute_error = TypeError("diagnostic assignment")
    logger_error = TypeError("diagnostic logging")
    unlink_error = TypeError("native unlink")

    class InitiatingError(Exception):
        def __setattr__(self, name, value):
            if name == "initialisation_cleanup_failures" and mode == "attribute":
                raise attribute_error
            super().__setattr__(name, value)

    failure = InitiatingError("marker write")

    def write(path, value, *, durable):
        if path is bot.INSTALLATION_MARKER_FILE:
            raise failure

    trace.atomic_write_json.side_effect = write
    errors = {
        bot.INSTALLATION_MARKER_FILE: PermissionError("marker residue"),
        base / "meme_post_receipt.exchange": FileNotFoundError(),
        base / "meme_post_receipt.ledger": IsADirectoryError("ledger residue"),
    }

    def unlink(path):
        if mode == "native_unlink":
            raise unlink_error
        if path in errors:
            raise errors[path]

    trace.unlink.side_effect = unlink
    if mode == "logger":
        trace.error.side_effect = logger_error
    with pytest.raises(Exception) as caught:
        bot.initialise_installation()
    expected_failure = {"original": failure, "logger": logger_error, "attribute": attribute_error, "native_unlink": unlink_error}[mode]
    assert caught.value is expected_failure
    created = [
        ".mrsMThatcher.initialising.json", "bot_state.json", "bot_state.tmp",
        "bot_state.json.bak1", "bot_state.json.bak2", "bot_state.json.bak1.tmp", "bot_state.json.bak2.tmp",
        "lines_used.json", "lines_used.json.tmp", "images_used.json", "images_used.json.tmp",
        "historical_context_reply_history.json", "historical_context_reply_outbox.json",
        "historical_context_reply_outbox.json.lock",
        "regular_post_receipt.ledger", "regular_post_receipt.exchange",
        "meme_post_receipt.ledger", "meme_post_receipt.exchange", ".mrsMThatcher.initialised.json",
    ]
    expected_unlinks = [call(base / name) for name in reversed(created)]
    assert trace.unlink.call_args_list == (expected_unlinks[:1] if mode == "native_unlink" else expected_unlinks)
    if mode in {"original", "logger"}:
        diagnostics = (
            f"{bot.INSTALLATION_MARKER_FILE}: PermissionError: marker residue",
            f"{base / 'meme_post_receipt.ledger'}: IsADirectoryError: ledger residue",
        )
        assert failure.initialisation_cleanup_failures == diagnostics
        trace.error.assert_called_once_with(
            "Initialisation rollback left exact non-file or unremovable paths: %s", "; ".join(diagnostics),
        )
    else:
        trace.error.assert_not_called()
    trace.fsync_parent_dir.assert_not_called()


def test_initialisation_keyboard_interrupt_remains_outside_rollback(monkeypatch, tmp_path):
    trace, _base, _receipts = _initialisation_trace(monkeypatch, tmp_path)
    failure = KeyboardInterrupt("interrupted data write")
    trace.save_state.side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.initialise_installation()
    assert caught.value is failure
    trace.unlink.assert_not_called()
    trace.atomic_write_json.assert_called_once_with(bot.INSTALLATION_IN_PROGRESS_FILE, {
        "schema_version": 1, "state": "initialising", "started_at_epoch": 100,
    }, durable=True)
