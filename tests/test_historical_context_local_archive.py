from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pytest

import historical_context_search_research as research
from historical_context_local_archive import (
    LOCAL_ARCHIVE_POLICY_VERSION,
    LocalArchiveError,
    LocalArchiveMirror,
    LocalMTFDocumentIndex,
)


def _mirror_root(tmp_path: Path) -> Path:
    root = tmp_path / "mirror"
    (root / "www.margaretthatcher.org" / "document").mkdir(parents=True)
    (root / "archive.margaretthatcher.org").mkdir()
    return root


def _document_html(
    document_id: str,
    passage: str,
    *,
    canonical_id: str | None = None,
) -> bytes:
    canonical_id = canonical_id or document_id
    filler = (
        "This is surrounding transcript material establishing a real document page "
        "with sufficient source context for deterministic validation. "
    )
    return f"""<!doctype html>
<html>
<head>
  <title>Article for Sunday Telegraph | Margaret Thatcher Foundation</title>
  <link rel="canonical"
        href="https://www.margaretthatcher.org/document%2F{canonical_id}">
</head>
<body class="node-type-archive-document">
  <main id="main">
    <header class="document-header">
      <h1>Article for Sunday Telegraph (&quot;How Tories will face the unions&quot;)</h1>
      <div class="docauthor">Margaret Thatcher</div>
      <div class="docdate"><time datetime="1977-05-15">1977 May 15 Su</time></div>
    </header>
    <article class="document-body"><p>{filler}{passage} {filler}</p></article>
  </main>
</body>
</html>""".encode()


def _write_document(root: Path, document_id: str, passage: str) -> Path:
    path = root / "www.margaretthatcher.org" / "document" / document_id
    path.write_bytes(_document_html(document_id, passage))
    return path


def test_local_mirror_reads_exact_document_without_recording_private_root(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    path = _write_document(root, "103384", "Political myths die hard.")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)

    record = mirror.read("http://margaretthatcher.org/document/103384")

    assert record is not None
    assert record["status"] == "fetched"
    assert record["final_url"] == "https://www.margaretthatcher.org/document/103384"
    assert record["local_archive_relative_path"] == (
        "www.margaretthatcher.org/document/103384"
    )
    assert record["local_archive_file_sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    assert record["network_attempt_count"] == 0
    encoded = json.dumps(mirror.configuration(), sort_keys=True)
    assert str(root) not in encoded
    assert mirror.configuration()["private_root_not_recorded"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/document/103384",
        "https://www.margaretthatcher.org/document/%2e%2e/secret",
        "https://www.margaretthatcher.org/document/103384?download=1",
        "https://user:password@www.margaretthatcher.org/document/103384",
    ],
)
def test_local_mirror_fails_closed_for_unsafe_or_unmapped_urls(
    tmp_path: Path,
    url: str,
) -> None:
    mirror = LocalArchiveMirror(_mirror_root(tmp_path), maximum_bytes=1024)
    with pytest.raises(LocalArchiveError):
        mirror.read(url)


def test_local_mirror_rejects_symlink_escape(tmp_path: Path) -> None:
    root = _mirror_root(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    link = root / "www.margaretthatcher.org" / "document" / "999999"
    link.symlink_to(outside)
    mirror = LocalArchiveMirror(root, maximum_bytes=1024)

    with pytest.raises(LocalArchiveError):
        mirror.read("https://www.margaretthatcher.org/document/999999")


def test_local_mirror_rejects_oversized_file_without_reading_as_evidence(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    path = root / "archive.margaretthatcher.org" / "large.pdf"
    path.write_bytes(b"%PDF-" + b"x" * 200)
    mirror = LocalArchiveMirror(root, maximum_bytes=100)

    record = mirror.read("https://archive.margaretthatcher.org/large.pdf")

    assert record is not None
    assert record["status"] == "local_archive_file_too_large"
    assert record["body"] == b""
    assert "local_archive_file_sha256" not in record


def test_local_mirror_supports_literal_percent_encoded_wget_filename(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    path = root / "archive.margaretthatcher.org" / "file%20name.txt"
    path.write_text("bounded archive text", encoding="utf-8")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024)

    record = mirror.read(
        "https://archive.margaretthatcher.org/file%20name.txt"
    )

    assert record is not None
    assert record["status"] == "fetched"
    assert record["body"] == b"bounded archive text"
    assert record["local_archive_relative_path"].endswith("file%20name.txt")


def test_document_inventory_ignores_malformed_and_non_numeric_names(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    _write_document(root, "103384", "Political myths die hard.")
    document = root / "www.margaretthatcher.org" / "document"
    (document / "103384&quot").write_text("bad mirror filename", encoding="utf-8")
    (document / "index.html").write_text("not a document", encoding="utf-8")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)

    assert list(mirror.iter_mtf_document_urls()) == [
        "https://www.margaretthatcher.org/document/103384"
    ]
    assert mirror.inventory().document_count == 1


def test_inventory_changes_when_a_document_file_changes(tmp_path: Path) -> None:
    root = _mirror_root(tmp_path)
    path = _write_document(root, "103384", "Political myths die hard.")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    before = mirror.inventory().sha256
    path.write_bytes(_document_html("103384", "Political myths are replaced."))
    after = mirror.inventory().sha256
    assert before != after


def test_file_that_changes_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mirror_root(tmp_path)
    path = _write_document(root, "103384", "Political myths die hard.")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    real_open = Path.open

    def changing_open(self: Path, *args: object, **kwargs: object):
        handle = real_open(self, *args, **kwargs)
        if self == path:
            stat = self.stat()
            os.utime(
                self,
                ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
            )
        return handle

    monkeypatch.setattr(Path, "open", changing_open)
    record = mirror.read("https://www.margaretthatcher.org/document/103384")
    assert record is not None
    assert record["status"] == "local_archive_incomplete_or_changing"
    assert record["body"] == b""


def test_current_mirror_mtf_layout_and_encoded_canonical_are_validated(
    tmp_path: Path,
) -> None:
    del tmp_path
    body = _document_html(
        "103384",
        "Political myths especially those cherished by political commentators die hard.",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384",
        "text/html",
        body,
    )
    assert validation["valid"] is True
    assert validation["selector_kind"] == "current_mirror"
    assert validation["document_number"] == "103384"
    extracted = research.extract_page_text({
        "status": "fetched",
        "content_type": "text/html",
        "body": body,
        "mtf_document_validation": validation,
    })
    assert extracted["status"] == "extracted"
    assert "Political myths" in extracted["text"]
    assert extracted["metadata"]["publisher"] == "Margaret Thatcher Foundation"


def test_encoded_canonical_for_wrong_document_is_rejected() -> None:
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384",
        "text/html",
        _document_html("103384", "Political myths die hard.", canonical_id="103385"),
    )
    assert validation["valid"] is False
    assert validation["status"] == "document_identity_mismatch"


def test_mtf_validation_selects_populated_transcript_over_empty_wrapper() -> None:
    body = _document_html(
        "103384",
        "Political myths die hard.",
    ).replace(
        b'<article class="document-body"><p>',
        b'<article class="document-body"></article>'
        b'<div id="documentheader"><h1>Legacy title</h1></div>'
        b'<div id="docauthor">Margaret Thatcher</div>'
        b'<div id="docdate">1977 May 15 Su</div>'
        b'<div id="documentbody"><p>',
    ).replace(
        b"</p></article>",
        b"</p></div>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384",
        "text/html",
        body,
    )
    assert validation["valid"] is True
    assert validation["selector_kind"] == "legacy"
    assert validation["title"] == "Legacy title"


def test_safe_fetcher_uses_local_document_without_dns_robots_or_page_budget(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    _write_document(
        root,
        "103384",
        "Political myths especially those cherished by political commentators die hard.",
    )
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)

    def unexpected_validator(_value: str) -> str:
        raise AssertionError("local hit must not perform DNS validation")

    budget = research.PageFetchBudget()
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=budget,
        url_validator=unexpected_validator,
        local_archive=mirror,
    )
    fetched = fetcher.fetch(
        "https://www.margaretthatcher.org/document/103384"
    )

    assert fetched["status"] == "fetched"
    assert fetched["local_archive_hit"] is True
    assert fetched["network_attempt_count"] == 0
    assert fetched["fetch_policy_version"] == research.FETCH_POLICY_VERSION
    assert fetched["mtf_document_validation"]["valid"] is True
    assert budget.as_dict()["unique_network_urls_requested"] == 0


def test_safe_fetcher_local_miss_falls_through_to_normal_validation(
    tmp_path: Path,
) -> None:
    mirror = LocalArchiveMirror(_mirror_root(tmp_path), maximum_bytes=1024)
    seen: list[str] = []

    def validator(value: str) -> str:
        seen.append(value)
        raise research.UnsafeURL("test stop after local miss")

    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        url_validator=validator,
        local_archive=mirror,
    )
    fetched = fetcher.fetch(
        "https://www.margaretthatcher.org/document/999999"
    )
    assert fetched["status"] == "unsafe_url"
    assert seen


def test_local_index_discovers_document_by_wording_without_document_id_hint(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    _write_document(
        root,
        "103384",
        (
            "Political myths especially those cherished by political commentators "
            "die hard. Expert analysis and prediction promptly created another myth."
        ),
    )
    _write_document(
        root,
        "108338",
        "This unrelated document discusses ordinary government business and elections.",
    )
    index = LocalMTFDocumentIndex(
        LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    )

    discovery = index.discover(
        "Political myths especially those cherished by political commentators die hard."
    )

    assert discovery["results"]
    assert discovery["results"][0]["document_id"] == "103384"
    assert discovery["results"][0]["result_url"].endswith("/document/103384")
    assert discovery["results"][0]["snippet"] == ""
    assert discovery["snippets_are_evidence"] is False
    assert all(anchor["token"] != "103384" for anchor in discovery["selected_anchors"])


def test_local_candidate_provenance_contains_no_private_absolute_path(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    quote = (
        "Political myths especially those cherished by political commentators die hard."
    )
    _write_document(root, "103384", quote)
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        local_archive=LocalArchiveMirror(root, maximum_bytes=1024 * 1024),
    )
    runner = object.__new__(research.SearchResearchRunner)
    runner.fetcher = fetcher
    result = {
        "canonical_url": "https://www.margaretthatcher.org/document/103384",
        "title": "",
    }
    candidate = runner._fetch_result({
        "quote_id": "synthetic-quote",
        "quotation_text": quote,
        "recorded_variants": [],
    }, result)

    assert candidate["local_archive_hit"] is True
    assert candidate["source_publisher"] == "Margaret Thatcher Foundation"
    assert candidate["retrieval_transport"] == "operator_managed_local_mirror"
    assert candidate["transport_url"].endswith("/document/103384")
    assert candidate["local_archive_relative_path"].endswith("/document/103384")
    assert str(root) not in json.dumps(candidate, sort_keys=True)


def test_runner_discovers_local_primary_before_any_paid_query(
    tmp_path: Path,
) -> None:
    root = _mirror_root(tmp_path)
    quote = (
        "Political myths especially those cherished by political commentators die hard."
    )
    _write_document(root, "103384", quote)
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    runner = object.__new__(research.SearchResearchRunner)
    runner.local_archive_index = LocalMTFDocumentIndex(mirror)
    runner.fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        local_archive=mirror,
    )
    runner.manifest = {"manifest_hash": "synthetic-manifest"}
    runner.targets = {
        "synthetic-quote": {
            "quote_id": "synthetic-quote",
            "quotation_text": quote,
            "recorded_variants": [],
        }
    }
    runner.state = {
        "cases": {
            "synthetic-quote": {
                "quote_id": "synthetic-quote",
                "status": "pending",
                "candidate_sources": [],
                "evaluated_canonical_urls": {},
            }
        },
        "ledger_records": [],
    }
    checkpoints: list[bool] = []
    runner.checkpoint = lambda: checkpoints.append(True)

    runner._process_local_archive_discovery()

    case = runner.state["cases"]["synthetic-quote"]
    assert case["local_archive_discovery"]["status"] == "complete"
    assert case["candidate_sources"][0]["classification"] == "strong_primary_evidence"
    assert case["status"] == "awaiting_codex_review"
    assert runner.state["ledger_records"][0]["backend"] == (
        "operator_owned_local_mtf_archive_index"
    )
    assert runner.state["ledger_records"][0]["snippet_is_evidence"] is False
    assert runner.fetcher.budget.as_dict()["unique_network_urls_requested"] == 0
    assert checkpoints


def test_configured_local_archive_uses_argument_or_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mirror_root(tmp_path)
    monkeypatch.setenv(research.LOCAL_ARCHIVE_ROOT_ENV, str(root))
    configured = research._configured_local_archive(
        argparse.Namespace(local_archive_root="")
    )
    assert configured is not None
    assert configured.configuration()["policy_version"] == LOCAL_ARCHIVE_POLICY_VERSION
