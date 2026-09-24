"""Import and binding contracts for main-post assembly."""

from pathlib import Path
import subprocess
import sys


def test_assembly_import_does_not_touch_runtime_authority():
    """Import the assembler without reading settings or touching the runtime."""
    code = """
import builtins, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('main-post assembly import touched runtime authority')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
import mrs_bot_main_post_assembly
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
