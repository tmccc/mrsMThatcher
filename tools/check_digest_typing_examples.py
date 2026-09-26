"""Check positive and negative digest examples with the production mypy config."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "typing"
COMMAND = [sys.executable, "-m", "mypy", "--config-file", str(ROOT / "mypy-digest.ini"), "--strict"]
DIAGNOSTIC = re.compile(r"^(.+):(\d+): error: .+\[([a-z-]+)\]$")
MARKER = re.compile(r"# expect: ([a-z-]+)$")


def check_positive() -> None:
    """Require the actual analysis and run transaction types to compose."""
    result = subprocess.run(
        [*COMMAND, str(FIXTURES / "digest_positive.py")],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise SystemExit(f"Positive digest typing example failed:\n{result.stdout}{result.stderr}")
    print("Positive digest typing example: passed")


def check_negative() -> None:
    """Require only the marked diagnostic codes at their marked lines."""
    fixture = FIXTURES / "digest_negative.py"
    expected: Counter[tuple[int, str]] = Counter()
    for number, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
        match = MARKER.search(line)
        if match:
            expected[(number, match.group(1))] += 1
    result = subprocess.run(
        [*COMMAND, str(fixture)], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    observed: Counter[tuple[int, str]] = Counter()
    for line in result.stdout.splitlines():
        match = DIAGNOSTIC.match(line)
        if match:
            path, number, code = match.groups()
            if Path(path).resolve() != fixture:
                raise SystemExit(f"Unexpected file in mypy diagnostics: {line}")
            observed[(int(number), code)] += 1
    if result.returncode == 0 or observed != expected:
        raise SystemExit(
            "Negative digest typing diagnostics differed.\n"
            f"Expected: {sorted(expected.items())}\nObserved: {sorted(observed.items())}\n"
            f"Full output:\n{result.stdout}{result.stderr}"
        )
    print(f"Negative digest typing example: {sum(expected.values())} intended violations detected")


if __name__ == "__main__":
    check_positive()
    check_negative()
