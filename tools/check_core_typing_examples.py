"""Check real typed-core examples with the same pinned mypy configuration.

Run with the interpreter that has requirements-dev.txt installed.  These files
are static fixtures and are never imported by the bot or pytest.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "typing"
COMMAND = [sys.executable, "-m", "mypy", "--config-file", str(ROOT / "mypy-core.ini"), "--strict"]
DIAGNOSTIC = re.compile(r"^(.+):(\d+): error: .+\[([a-z-]+)\]$")


def check_positive() -> None:
    """Require the real callbacks and accepted variants to type-check."""
    result = subprocess.run(
        [*COMMAND, str(FIXTURES / "positive_core.py")],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"Positive core typing example failed:\n{result.stdout}{result.stderr}")
    print("Positive core typing example: passed")


def check_negative() -> None:
    """Require each intended contract error at its fixture location."""
    fixture = FIXTURES / "negative_core.py"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    expected = {
        (next(i for i, line in enumerate(lines, 1) if 'state["daily_repl_count"]' in line), "typeddict-item"),
        (next(i for i, line in enumerate(lines, 1) if 'context["target_id"] = 7' in line), "typeddict-item"),
        (next(i for i, line in enumerate(lines, 1) if 'finalise(state, receipt' in line), "arg-type"),
        (next(i for i, line in enumerate(lines, 1) if 'return outcome.reply' in line), "return-value"),
        (next(i for i, line in enumerate(lines, 1) if 'bad_storage: StoreReplyDraft' in line), "assignment"),
        (next(i for i, line in enumerate(lines, 1) if 'return _unsupported_outcome(outcome)' in line), "arg-type"),
    }
    result = subprocess.run(
        [*COMMAND, str(fixture)], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    observed = set()
    for line in result.stdout.splitlines():
        match = DIAGNOSTIC.match(line)
        if match:
            path, line_number, code = match.groups()
            if Path(path).resolve() != fixture:
                raise SystemExit(f"Unexpected file in mypy diagnostics: {line}")
            observed.add((int(line_number), code))
    if result.returncode == 0 or observed != expected:
        raise SystemExit(
            "Negative core typing diagnostics differed.\n"
            f"Expected: {sorted(expected)}\nObserved: {sorted(observed)}\n"
            f"Full output:\n{result.stdout}{result.stderr}"
        )
    print(f"Negative core typing example: {len(expected)} intended violations detected")


if __name__ == "__main__":
    check_positive()
    check_negative()
