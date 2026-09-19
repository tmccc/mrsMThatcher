"""Independent attacks on exact generation identity and legacy ambiguity."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import mrs_bot_state_generation as generations
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


@pytest.mark.parametrize('scalar', [True, 1.0])
@pytest.mark.parametrize('operation', ['load', 'save'])
def test_legacy_python_equal_scalars_do_not_prove_same_document(monkeypatch, scalar, operation):
    """Python equality between booleans/numbers cannot authorise migration."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 1)
    paths = [bot.STATE_FILE, bot.STATE_FILE.with_name(bot.STATE_FILE.name + '.bak1')]
    for path, value in zip(paths, [1, scalar]):
        path.write_text(json.dumps({'extension': value}), encoding='utf-8')
        path.chmod(0o600)
    before = [path.read_bytes() for path in paths]
    with pytest.raises(RuntimeError, match='ambiguous|diverge'):
        if operation == 'load':
            bot.load_state()
        else:
            bot.save_state(bot.default_state(), durable=True)
    assert [path.read_bytes() for path in paths] == before


def test_post_commit_identity_capture_cannot_bless_substitute_inode(monkeypatch):
    """A byte-identical replacement must not inherit the original fsync proof."""
    monkeypatch.setattr(bot, 'STATE_BACKUP_COUNT', 0)
    state = bot.default_state()
    bot.save_state(state, durable=True)
    original = generations.file_identity
    substituted = False

    def replace_at_capture(path: Path):
        """Replace after committed inode comparison, before proof revalidation."""
        nonlocal substituted
        if path == bot.STATE_FILE and not substituted:
            substituted = True
            replacement = path.with_name('unfsynced-substitute.json')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, path)
        return original(path)

    monkeypatch.setattr(generations, 'file_identity', replace_at_capture)
    with pytest.raises(RuntimeError, match='identity changed'):
        bot.save_state(state, durable=True)
    assert substituted
