"""Keep production corpus validation independent of offline research packages."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_real_corpus_loading_without_research_dependencies_or_network():
    """A fresh interpreter validates the real corpus with research imports denied."""
    root = Path(__file__).resolve().parents[1]
    script = '''
import builtins
from pathlib import Path
import socket
import sys
import requests

def no_network(*args, **kwargs):
    raise AssertionError("runtime corpus validation attempted network access")

socket.create_connection = socket.getaddrinfo = no_network
socket.socket.connect = socket.socket.connect_ex = no_network
requests.sessions.Session.request = no_network
blocked = {"bs4", "lxml", "fitz", "pymupdf", "google", "openai", "numpy", "onnxruntime", "tokenizers", "httpx", "jsonschema", "jinja2", "imagehash", "fastapi", "starlette", "uvicorn"}
original_import = builtins.__import__
def production_only_import(name, *args, **kwargs):
    if name.split(".", 1)[0] in blocked:
        raise ModuleNotFoundError("offline research dependency unavailable: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = production_only_import

from historical_context_formatter import load_and_validate_corpus
packets, unresolved = load_and_validate_corpus(
    Path(sys.argv[1]), require_source_role_audit=True,
)
assert packets
assert any("_source_role_audit" in packet for packet in packets.values())
assert "historical_context_source_resolution" in sys.modules
assert "historical_context_search_research" not in sys.modules
assert "public_source_fetch" not in sys.modules
assert not blocked.intersection(sys.modules)
print("PRODUCTION_CORPUS_VALIDATED")
'''
    result = subprocess.run(
        [sys.executable, '-c', script, str(root / 'semantic_alignment_research/quote_research_full_001')],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PRODUCTION_CORPUS_VALIDATED' in result.stdout
