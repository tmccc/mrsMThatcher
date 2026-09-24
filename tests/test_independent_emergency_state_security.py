"""Adversarial checks of composite confirmed-state retirement authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import exact_receipt_retirement as retirement
from mrs_bot_state_generation import record_receipt_commit
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.receipt_fixtures import production_lane_documents


@pytest.fixture(autouse=True)
def private_history_paths(tmp_path, monkeypatch):
    """Keep both authority histories private to each test's temporary directory."""
    monkeypatch.setattr(bot, 'LINES_USED_FILE', tmp_path / 'lines_used.json')
    monkeypatch.setattr(bot, 'IMAGES_USED_FILE', tmp_path / 'images_used.json')


def prepared_regular_receipt():
    """Publish a real confirmed effect and prepare its exact retirement marker."""
    _source, receipt, _source_bytes, receipt_bytes, _post_id = production_lane_documents('quote_image')
    state = bot.default_state()
    lines, images = set(), set()
    bot._main_post_assembly().recovery_operation().apply_regular(receipt, lines, images, state)
    record_receipt_commit(state, receipt)
    result = bot.emergency_persist_confirmed_regular_post(lines, images, state)
    assert result.failures == ()
    assert result.commit_proof is not None
    bot.atomic_write_json(bot.REGULAR_POST_RECEIPT_FILE, receipt, durable=True)
    authority = bot.transaction_mutation_authority('independent retirement regression')
    retirement.initialise_retirement_ledger(bot.REGULAR_POST_RECEIPT_FILE, mutation_authority=authority)
    retirement.prepare_exact_receipt_retirement(
        bot.REGULAR_POST_RECEIPT_FILE, receipt_bytes, mutation_authority=authority,
    )
    return receipt, result.commit_proof


@pytest.mark.parametrize('representation', ['mapping', 'string', 'unhashable_item'])
def test_restart_retirement_rejects_unreadable_used_history(representation):
    """Membership in malformed JSON is not a usable duplicate-suppression set."""
    receipt, _proof = prepared_regular_receipt()
    identity = receipt['quote_hash']
    malformed = {
        'mapping': {identity: True},
        'string': identity,
        'unhashable_item': [identity, {}],
    }[representation]
    bot.atomic_write_json(bot.LINES_USED_FILE, malformed, durable=True)
    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(bot.LINES_USED_FILE)
    with pytest.raises((ValueError, RuntimeError)):
        bot.resume_interrupted_source_receipt_retirement_if_present()
    assert retirement.retirement_auxiliary_barrier_exists(bot.REGULAR_POST_RECEIPT_FILE)


def test_emergency_proof_rejects_equal_history_inode_replacement(monkeypatch, tmp_path):
    """The last source removal cannot use an equal but newly substituted history."""
    receipt, proof = prepared_regular_receipt()
    original = retirement._read_stable_entry
    changed = False

    def replace_after_validation(*args, **kwargs):
        """Change history after initial proof but before the destructive transition."""
        nonlocal changed
        entry = original(*args, **kwargs)
        if not changed:
            replacement = tmp_path / 'unfsynced-history.json'
            replacement.write_bytes(bot.LINES_USED_FILE.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, bot.LINES_USED_FILE)
            changed = True
        return entry

    monkeypatch.setattr(retirement, '_read_stable_entry', replace_after_validation)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        bot.remove_regular_post_receipt(receipt, commit_proof=proof)
    assert changed
    assert retirement.retirement_auxiliary_barrier_exists(bot.REGULAR_POST_RECEIPT_FILE)


def test_emergency_proof_does_not_authorise_an_unrelated_receipt():
    """A current complete state still requires the exact confirmed source digest."""
    receipt, proof = prepared_regular_receipt()
    unrelated = {**receipt, 'post_id': '999999'}
    with pytest.raises(RuntimeError, match='exact receipt'):
        bot.remove_regular_post_receipt(unrelated, commit_proof=proof)
    assert retirement.retirement_auxiliary_barrier_exists(bot.REGULAR_POST_RECEIPT_FILE)


@pytest.mark.parametrize('primary_condition', ['missing', 'corrupt'])
@pytest.mark.parametrize('operation', ['load', 'save'])
def test_legacy_backup_numbers_do_not_prove_generation_order(monkeypatch, primary_condition, operation):
    """A previous failed replica publication can make bak2 newer than bak1."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    if primary_condition == 'corrupt':
        bot.STATE_FILE.write_text('{interrupted', encoding='utf-8')
        bot.STATE_FILE.chmod(0o600)
    backups = [bot.STATE_FILE.with_name(bot.STATE_FILE.name + f'.bak{number}') for number in (1, 2)]
    for path, post_id in zip(backups, ('111', '222')):
        path.write_text(json.dumps({'replied_to_ids': [post_id]}), encoding='utf-8')
        path.chmod(0o600)
    before = {path: path.read_bytes() for path in (bot.STATE_FILE, *backups) if path.exists()}
    with pytest.raises(RuntimeError, match='ambiguous|diverg|generation'):
        if operation == 'load':
            bot.load_state()
        else:
            bot.save_state(bot.default_state(), durable=True)
    assert {path: path.read_bytes() for path in (bot.STATE_FILE, *backups) if path.exists()} == before


@pytest.mark.parametrize('operation', ['load', 'save'])
def test_legacy_primary_without_latest_replica_has_no_ordering_authority(monkeypatch, operation):
    """A primary without its committed replica cannot order a divergent backup."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    older = bot.STATE_FILE.with_name(bot.STATE_FILE.name + '.bak2')
    for path, post_id in ((bot.STATE_FILE, '111'), (older, '222')):
        bot.atomic_write_json(path, {'replied_to_ids': [post_id]}, durable=True)
    with pytest.raises(RuntimeError, match='ambiguous'):
        if operation == 'load':
            bot.load_state()
        else:
            bot.save_state(bot.default_state(), durable=True)


@pytest.mark.parametrize('layout', ['agreeing_backups', 'committed_primary_pair'])
def test_provable_legacy_agreement_migrates(monkeypatch, layout):
    """Equal backups or the completed canonical/latest pair retain availability."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    primary, backup1, backup2 = [bot.STATE_FILE, *(bot.STATE_FILE.with_name(bot.STATE_FILE.name + f'.bak{number}') for number in (1, 2))]
    documents = [(backup1, '222'), (backup2, '222')]
    if layout == 'committed_primary_pair':
        documents = [(primary, '222'), (backup1, '222'), (backup2, '111')]
    for path, post_id in documents:
        bot.atomic_write_json(path, {'replied_to_ids': [post_id]}, durable=True)
    state = bot.load_state()
    assert state['replied_to_ids'] == ['222']
    assert state['_state_generation']['sequence'] >= 1
    assert primary.read_bytes() == backup1.read_bytes()


def test_repairable_legacy_pending_corruption_does_not_authorise_backup_order(monkeypatch):
    """Discardable pending metadata cannot erase ambiguity in durable identities."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    for number, post_id in ((1, '111'), (2, '222')):
        path = bot.STATE_FILE.with_name(bot.STATE_FILE.name + f'.bak{number}')
        bot.atomic_write_json(path, {'replied_to_ids': [post_id], 'mention_pending_candidates': 'invalid'}, durable=True)
    with pytest.raises(RuntimeError, match='ambiguous'):
        bot.load_state()
    assert not bot.STATE_FILE.exists()


@pytest.mark.parametrize('collision', [False, True])
def test_repairable_modern_generations_keep_order_and_sequence(monkeypatch, collision):
    """Recovery selects the newest sealed generation and cannot reuse its number."""
    from mrs_bot_state_generation import encode_generation
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    for number, sequence, post_id in ((1, 8 if collision else 7, '111'), (2, 8, '222')):
        path = bot.STATE_FILE.with_name(bot.STATE_FILE.name + f'.bak{number}')
        state = bot.state_document_for_persistence(bot.default_state())
        state.update(replied_to_ids=[post_id], mention_pending_candidates='invalid')
        _document, encoded = encode_generation(state, sequence, bot.DURABLE_RUNTIME_JSON_MAX_BYTES)
        path.write_bytes(encoded)
        path.chmod(0o600)
    if collision:
        with pytest.raises(RuntimeError, match='conflicting state generation'):
            bot.load_state()
    else:
        state = bot.load_state()
        assert state['replied_to_ids'] == ['222']
        assert state['_state_generation']['sequence'] > 8


def test_legacy_backup_ambiguity_remains_blocking_in_a_fresh_process(monkeypatch):
    """No process-local memory may make filename-ordered legacy recovery safe."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    paths = [bot.STATE_FILE.with_name(bot.STATE_FILE.name + f'.bak{number}') for number in (1, 2)]
    for path, post_id in zip(paths, ('111', '222')):
        bot.atomic_write_json(path, {'replied_to_ids': [post_id]}, durable=True)
    before = {path: path.read_bytes() for path in paths}
    result = subprocess.run(
        [sys.executable, '-c', '''
from pathlib import Path
import sys
from tests.helpers.bot_runtime import bot
bot.STATE_FILE = Path(sys.argv[1])
bot.STATE_BACKUP_COUNT = 2
try:
    bot.load_state()
except RuntimeError as exc:
    assert "ambiguous legacy" in str(exc), str(exc)
    print("BLOCKED_AMBIGUOUS_LEGACY")
else:
    raise AssertionError("fresh process guessed a legacy generation")
''', str(bot.STATE_FILE)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'BLOCKED_AMBIGUOUS_LEGACY' in result.stdout
    assert not bot.STATE_FILE.exists()
    assert {path: path.read_bytes() for path in paths} == before
