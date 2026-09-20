"""Exercise restart authority at state publication failure boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import mrs_bot_state_persistence as state_persistence
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def snapshots(path: Path) -> dict[str, bytes]:
    """Capture only the primary and published backups."""
    return {p.name: p.read_bytes() for p in path.parent.glob(path.name + '*')}


def test_committed_primary_survives_backup_failure(monkeypatch):
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    state = bot.default_state()
    bot.save_state(state, durable=True)
    state['replied_to_ids'] = ['123']
    with monkeypatch.context() as patch:
        patch.setattr(
            state_persistence.StateBackups,
            'write_latest',
            lambda _owner, **kw: (_ for _ in ()).throw(OSError('disk full')),
        )
        with pytest.raises(bot.StateBackupWriteError):
            bot.save_state(state, durable=True)
    assert bot.load_state()['replied_to_ids'] == ['123']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_save_leaves_all_generations_unchanged(value, monkeypatch):
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    state = bot.default_state()
    bot.save_state(state, durable=True)
    before = snapshots(bot.STATE_FILE)
    state['extension'] = value
    with pytest.raises((ValueError, TypeError)):
        bot.save_state(state, durable=True)
    assert snapshots(bot.STATE_FILE) == before


@pytest.mark.parametrize('field,value', [('replied_to_ids', {}), ('daily_reply_count', True), ('tweet_cache', [])])
def test_reader_rejected_state_cannot_replace_primary(field, value):
    state = bot.default_state()
    bot.save_state(state, durable=True)
    before = snapshots(bot.STATE_FILE)
    state[field] = value
    with pytest.raises((ValueError, TypeError)):
        bot.save_state(state, durable=True)
    assert snapshots(bot.STATE_FILE) == before


def test_emergency_match_rejects_symlink(tmp_path):
    state = bot.default_state()
    bot.save_state(state, durable=True)
    other = tmp_path / 'other.json'
    bot.STATE_FILE.rename(other)
    bot.STATE_FILE.symlink_to(other)
    assert not bot.json_file_matches(bot.STATE_FILE, state)


@pytest.mark.parametrize('mode', [0o620, 0o602, 0o666])
def test_writable_state_namespace_rejected(mode):
    bot.save_state(bot.default_state(), durable=True)
    bot.STATE_FILE.chmod(mode)
    with pytest.raises(bot.UnsafeDurableStateNamespace):
        bot.load_state()


def fresh_process_state(path: Path) -> dict:
    """Load the same temporary state in a new interpreter with dead test endpoints."""
    code = '''
import json, sys
from pathlib import Path
from tests.helpers.bot_runtime import bot
bot.STATE_FILE = Path(sys.argv[1])
bot.STATE_BACKUP_COUNT = 2
print('RESULT=' + json.dumps(bot.load_state()))
'''
    result = subprocess.run([sys.executable, '-c', code, str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith('RESULT=')))


@pytest.mark.parametrize('boundary', ['before_replace', 'after_replace', 'before_dir_fsync',
                                      'after_dir_fsync', 'backup_creation', 'after_backup'])
def test_process_restart_at_each_commit_boundary(boundary, monkeypatch):
    """An interrupted complete primary wins even when the replica was not published."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    state = bot.default_state()
    bot.save_state(state, durable=True)
    state['replied_to_ids'] = ['456']
    original_replace = os.replace
    original_fsync_parent = bot.fsync_parent_dir
    original_backup = bot._state_backups_owner().write_latest

    def replace(source, destination):
        """Inject termination on either side of the canonical atomic replacement."""
        if destination == bot.STATE_FILE and boundary == 'before_replace':
            raise KeyboardInterrupt('before canonical replace')
        original_replace(source, destination)
        if destination == bot.STATE_FILE and boundary == 'after_replace':
            raise KeyboardInterrupt('after canonical replace')

    def fsync_parent(path, **options):
        """Inject termination around the canonical directory durability boundary."""
        if path == bot.STATE_FILE and boundary == 'before_dir_fsync':
            raise KeyboardInterrupt('before fsync')
        original_fsync_parent(path, **options)
        if path == bot.STATE_FILE and boundary == 'after_dir_fsync':
            raise KeyboardInterrupt('after fsync')

    def backup(_owner, **options):
        """Inject failure during replica publication or after it completes."""
        if boundary == 'backup_creation':
            raise OSError('replica failed')
        original_backup(**options)
        if boundary == 'after_backup':
            raise KeyboardInterrupt('after backup')

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        patch.setattr(bot, 'fsync_parent_dir', fsync_parent)
        patch.setattr(state_persistence.StateBackups, 'write_latest', backup)
        with pytest.raises((KeyboardInterrupt, bot.StateBackupWriteError)):
            bot.save_state(state, durable=True)
    expected = [] if boundary == 'before_replace' else ['456']
    assert fresh_process_state(bot.STATE_FILE)['replied_to_ids'] == expected
    assert bot.load_state()['replied_to_ids'] == expected


def test_exact_reader_limit_and_one_byte_over_are_atomic(monkeypatch):
    """Unbounded identity/extension growth is checked before any durable mutation."""
    from mrs_bot_state_generation import encode_generation
    state = bot.default_state()
    state['replied_to_ids'] = [str(10**20 + index) for index in range(1000)]
    state['padding'] = 'x' * 5000
    document, data = encode_generation(bot.state_document_for_persistence(state), 1, 100000)
    monkeypatch.setattr(bot, 'DURABLE_RUNTIME_JSON_MAX_BYTES', len(data))
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    bot.save_state(state, durable=True)
    assert bot.STATE_FILE.stat().st_size == len(data)
    assert bot.load_state()['replied_to_ids'] == state['replied_to_ids']
    before = snapshots(bot.STATE_FILE)
    state['padding'] += 'x'
    with pytest.raises(ValueError, match='byte limit'):
        bot.save_state(state, durable=True)
    assert snapshots(bot.STATE_FILE) == before
    assert bot.load_state()['padding'] == document['padding']


@pytest.mark.parametrize('substitution', ['replace', 'symlink', 'hardlink', 'permissions'])
def test_commit_proof_refuses_namespace_substitution(substitution, tmp_path):
    """Equal content cannot substitute for the exact committed file authority."""
    proof = bot.save_state(bot.default_state(), durable=True)
    other = tmp_path / 'replacement'
    other.write_bytes(bot.STATE_FILE.read_bytes())
    other.chmod(0o600)
    if substitution == 'replace':
        os.replace(other, bot.STATE_FILE)
    elif substitution == 'symlink':
        bot.STATE_FILE.unlink()
        bot.STATE_FILE.symlink_to(other)
    elif substitution == 'hardlink':
        other.unlink()
        os.link(bot.STATE_FILE, other)
    else:
        bot.STATE_FILE.chmod(0o660)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        proof.require_current()


def test_directory_permissions_are_authority(tmp_path):
    """A writable directory cannot confer authority on otherwise private files."""
    bot.save_state(bot.default_state(), durable=True)
    tmp_path.chmod(0o770)
    try:
        with pytest.raises(bot.UnsafeDurableStateNamespace):
            bot.load_state()
    finally:
        tmp_path.chmod(0o700)


def test_state_substitution_during_receipt_retirement_retains_barrier(tmp_path, monkeypatch):
    """A substitution after inspection is caught at the destructive transition."""
    import exact_receipt_retirement as retirement
    proof = bot.save_state(bot.default_state(), durable=True)
    receipt = tmp_path / 'receipt.json'
    bot.atomic_write_json(receipt, {'confirmed': '123'}, durable=True)
    original = retirement._read_stable_entry
    changed = False

    def inspect(*args, **kwargs):
        """Replace the canonical generation after the initial authority check."""
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            replacement = tmp_path / 'replacement.json'
            replacement.write_bytes(bot.STATE_FILE.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, bot.STATE_FILE)
            changed = True
        return result

    monkeypatch.setattr(retirement, '_read_stable_entry', inspect)
    with pytest.raises(RuntimeError):
        bot.retire_current_source_receipt(receipt, bot.canonical_atomic_json_bytes({'confirmed': '123'}),
                                          commit_proof=proof)
    assert receipt.exists() or retirement.retirement_auxiliary_barrier_exists(receipt)


def test_used_history_growth_is_rejected_before_replacement(tmp_path, monkeypatch):
    """Unbounded identity history cannot publish a document its reader rejects."""
    path = tmp_path / 'used.json'
    original = {'a' * 64}
    bot.save_quote_used_hashes(path, original, durable=True)
    before = path.read_bytes()
    monkeypatch.setattr(bot, 'DURABLE_RUNTIME_JSON_MAX_BYTES', len(before))
    with pytest.raises(ValueError, match='reader byte limit'):
        bot.save_quote_used_hashes(path, original | {'b' * 64}, durable=True)
    assert path.read_bytes() == before
    assert bot.read_stable_owned_json_bytes_no_follow(path) == (True, before)


def test_save_refuses_unknown_future_disk_reader_authority():
    """An older runtime snapshot cannot replace a future-reader disk document."""
    state = bot.default_state()
    bot.save_state(state, durable=True)
    disk = json.loads(bot.STATE_FILE.read_bytes())
    disk['minimum_reader_version'] = 999
    # Future formats need not understand or preserve today's generation envelope.
    disk.pop('_state_generation')
    bot.atomic_write_json(bot.STATE_FILE, disk, durable=True)
    before = snapshots(bot.STATE_FILE)
    with pytest.raises(RuntimeError, match='reader'):
        bot.save_state(state, durable=True)
    assert snapshots(bot.STATE_FILE) == before


@pytest.mark.parametrize('boundary', ['temporary_fsync', 'replacement', 'directory_fsync'])
def test_backup_internal_failures_leave_committed_primary_restart_safe(boundary, monkeypatch):
    """Fault inside replica creation, after its staged file exists, not just its caller."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    state = bot.default_state()
    bot.save_state(state, durable=True)
    state['replied_to_ids'] = ['789']
    backup = bot.STATE_FILE.with_name(bot.STATE_FILE.name + '.bak1')
    real_fsync, real_replace, real_parent = os.fsync, os.replace, bot.fsync_parent_dir

    def fsync(descriptor):
        """Fail the exact backup temporary inode's durability acknowledgement."""
        name = Path(os.readlink(f'/proc/self/fd/{descriptor}')).name
        if boundary == 'temporary_fsync' and name.startswith(f'.{backup.name}.'):
            raise OSError('backup temporary fsync failed')
        return real_fsync(descriptor)

    def replace(source, destination):
        """Fail after backup creation but before publishing its name."""
        if boundary == 'replacement' and destination == backup:
            raise OSError('backup replacement failed')
        return real_replace(source, destination)

    def parent(path, **options):
        """Fail after backup replacement without invalidating canonical authority."""
        if boundary == 'directory_fsync' and path == backup:
            raise OSError('backup directory fsync failed')
        return real_parent(path, **options)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fsync)
        patch.setattr(os, 'replace', replace)
        patch.setattr(bot, 'fsync_parent_dir', parent)
        with pytest.raises(bot.StateBackupWriteError) as caught:
            bot.save_state(state, durable=True)
        caught.value.commit_proof.require_current()
    assert fresh_process_state(bot.STATE_FILE)['replied_to_ids'] == ['789']


def test_hard_process_exit_after_primary_replacement(monkeypatch):
    """A real process exit skips all Python cleanup and leaves an ordered primary."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 2)
    bot.save_state(bot.default_state(), durable=True)
    script = '''
import os, sys
from pathlib import Path
from tests.helpers.bot_runtime import bot
bot.STATE_FILE = Path(sys.argv[1])
bot.STATE_BACKUP_COUNT = 2
state = bot.load_state()
state['replied_to_ids'] = ['987']
replace = os.replace
def terminate_after_replace(source, destination):
    replace(source, destination)
    if destination == bot.STATE_FILE:
        os._exit(73)
os.replace = terminate_after_replace
bot.save_state(state, durable=True)
'''
    result = subprocess.run([sys.executable, '-c', script, str(bot.STATE_FILE)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stdout + result.stderr
    assert fresh_process_state(bot.STATE_FILE)['replied_to_ids'] == ['987']
