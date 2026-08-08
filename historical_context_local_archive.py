#!/usr/bin/env python3
"""Fail-closed access and deterministic discovery for an operator-owned web mirror.

The mirror is a transport copy, not a publisher or an evidence decision.  This
module deliberately knows nothing about canonical research records.  It maps a
small allowlist of public Margaret Thatcher Foundation URLs to regular files,
returns bounded bytes with provenance, and builds a page-level discovery index
over exact numeric ``/document/<id>`` HTML files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit

from bs4 import BeautifulSoup


LOCAL_ARCHIVE_POLICY_VERSION = "historical-context-local-archive-v1"
LOCAL_ARCHIVE_ROOT_ENV = "MRS_MTHATCHER_LOCAL_ARCHIVE_ROOT"
MARGARET_THATCHER_FOUNDATION_PUBLISHER = "Margaret Thatcher Foundation"
ALLOWED_ARCHIVE_HOSTS = frozenset({
    "margaretthatcher.org",
    "www.margaretthatcher.org",
    "archive.margaretthatcher.org",
})
HOST_DIRECTORY = {
    "margaretthatcher.org": "www.margaretthatcher.org",
    "www.margaretthatcher.org": "www.margaretthatcher.org",
    "archive.margaretthatcher.org": "archive.margaretthatcher.org",
}
_DOCUMENT_PATH = re.compile(r"/document/([0-9]+)", re.ASCII)
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.ASCII)
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
    "by", "for", "from", "had", "has", "have", "he", "her", "hers", "him",
    "his", "i", "if", "in", "into", "is", "it", "its", "me", "my", "no",
    "not", "of", "on", "or", "our", "ours", "she", "so", "that", "the",
    "their", "theirs", "them", "there", "they", "this", "those", "to", "us",
    "was", "we", "were", "what", "when", "which", "who", "will", "with",
    "you", "your",
})


class LocalArchiveError(RuntimeError):
    """A configured mirror is unsafe, incomplete or internally inconsistent."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    )


def _normalise_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\N{SOFT HYPHEN}", "")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    return " ".join(text.casefold().split())


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(_normalise_text(value))


def _contains_contiguous(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(list(haystack[start:start + width]) == list(needle)
               for start in range(0, len(haystack) - width + 1))


def _safe_url_parts(value: str) -> tuple[str, str, tuple[str, ...]]:
    """Return canonical URL, host-directory and decoded safe path components."""
    if not isinstance(value, str) or not value.strip():
        raise LocalArchiveError("local archive URL is empty")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or host not in ALLOWED_ARCHIVE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80, 443}
        or parsed.fragment
    ):
        raise LocalArchiveError("URL is outside the local archive allowlist")
    if parsed.query:
        raise LocalArchiveError("query-bearing URLs are not mapped into the local archive")
    try:
        decoded = unquote(parsed.path or "/", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise LocalArchiveError("URL path is not valid encoded text") from exc
    if "\x00" in decoded or "\\" in decoded or any(ord(char) < 32 for char in decoded):
        raise LocalArchiveError("URL path contains unsafe characters")
    components = tuple(component for component in decoded.split("/") if component)
    if any(component in {".", ".."} for component in components):
        raise LocalArchiveError("URL path traversal is forbidden")
    canonical_path = "/" + "/".join(components)
    canonical = urlunsplit(("https", "www.margaretthatcher.org"
                            if host == "margaretthatcher.org" else host,
                            canonical_path or "/", "", ""))
    return canonical, HOST_DIRECTORY[host], components


def _content_type(path: Path, body_prefix: bytes) -> str:
    suffix = path.suffix.casefold()
    stripped = body_prefix.lstrip()
    folded = stripped[:64].lower()
    if (
        suffix in {".html", ".htm"}
        or b"<!doctype html" in folded
        or b"<html" in folded
    ):
        return "text/html"
    if suffix == ".pdf" or body_prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".json" or stripped.startswith((b"{", b"[")):
        return "application/json"
    if suffix in {".xml", ".rss"} or stripped.startswith(b"<?xml"):
        return "application/xml"
    if suffix in {".txt", ".text", ""}:
        return "text/plain"
    return "application/octet-stream"


@dataclass(frozen=True)
class LocalArchiveInventory:
    """Stable identity for the currently complete-looking document-file set."""

    sha256: str
    document_count: int
    total_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            "sha256": self.sha256,
            "document_count": self.document_count,
            "total_bytes": self.total_bytes,
        }


class LocalArchiveMirror:
    """Map allowlisted public URLs to bounded regular files beneath one root."""

    def __init__(self, root: Path | str, *, maximum_bytes: int) -> None:
        supplied = Path(root)
        if not supplied.is_absolute():
            raise LocalArchiveError("local archive root must be absolute")
        if not supplied.is_dir():
            raise LocalArchiveError("local archive root is not a directory")
        self.root = supplied.resolve(strict=True)
        self.maximum_bytes = int(maximum_bytes)
        if self.maximum_bytes <= 0:
            raise LocalArchiveError("local archive byte limit must be positive")
        for directory in sorted(set(HOST_DIRECTORY.values())):
            child = self.root / directory
            if child.exists() and (not child.is_dir() or child.is_symlink()):
                raise LocalArchiveError(f"unsafe local archive host directory: {directory}")

    @property
    def root_identity_sha256(self) -> str:
        return _sha256_bytes(os.fsencode(str(self.root)))

    def _path_candidates_for(self, value: str) -> list[tuple[str, Path, str]]:
        canonical, directory, components = _safe_url_parts(value)
        host_root = (self.root / directory).resolve(strict=False)
        parsed = urlsplit(value.strip())
        raw_components = tuple(
            component for component in (parsed.path or "/").split("/") if component
        )
        component_options = [components]
        if raw_components != components:
            component_options.append(raw_components)
        output: list[tuple[str, Path, str]] = []
        for option in component_options:
            if any(
                component in {".", ".."}
                or "\x00" in component
                or "\\" in component
                for component in option
            ):
                raise LocalArchiveError("URL path contains unsafe raw components")
            candidate = host_root.joinpath(*option)
            resolved = candidate.resolve(strict=False)
            try:
                relative = resolved.relative_to(self.root)
                resolved.relative_to(host_root)
            except ValueError as exc:
                raise LocalArchiveError(
                    "local archive path escapes its host directory"
                ) from exc
            item = (canonical, resolved, relative.as_posix())
            if item not in output:
                output.append(item)
            # HTTrack commonly stores an extensionless public MTF document URL
            # as ``document/<id>.html``.  Keep the public identity
            # extensionless and add only this exact numeric representation.
            if (
                len(option) >= 2
                and option[-2] == "document"
                and re.fullmatch(r"[0-9]+", option[-1])
            ):
                html_candidate = candidate.with_name(candidate.name + ".html")
                html_resolved = html_candidate.resolve(strict=False)
                try:
                    html_relative = html_resolved.relative_to(self.root)
                    html_resolved.relative_to(host_root)
                except ValueError as exc:
                    raise LocalArchiveError(
                        "local archive HTML representation escapes its host directory"
                    ) from exc
                html_item = (canonical, html_resolved, html_relative.as_posix())
                if html_item not in output:
                    output.append(html_item)
        return output

    def _path_for(self, value: str) -> tuple[str, Path, str]:
        candidates = self._path_candidates_for(value)
        return next(
            (item for item in candidates if item[1].exists()),
            candidates[0],
        )

    def read(self, value: str) -> dict[str, Any] | None:
        """Read one stable bounded file, returning None only for a genuine miss."""
        canonical, path, relative = self._path_for(value)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise LocalArchiveError("local archive URL does not resolve to a regular file")
        before = path.stat()
        if before.st_size > self.maximum_bytes:
            return {
                "status": "local_archive_file_too_large",
                "requested_url": canonical,
                "final_url": canonical,
                "content_type": _content_type(path, b""),
                "captured_body_bytes": 0,
                "local_archive_hit": True,
                "local_archive_relative_path": relative,
                "local_archive_policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
                "network_attempt_count": 0,
                "retry_count": 0,
                "body": b"",
            }
        with path.open("rb") as handle:
            body = handle.read(self.maximum_bytes + 1)
        after = path.stat()
        if (
            len(body) > self.maximum_bytes
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(body) != after.st_size
        ):
            return {
                "status": "local_archive_incomplete_or_changing",
                "requested_url": canonical,
                "final_url": canonical,
                "captured_body_bytes": 0,
                "local_archive_hit": True,
                "local_archive_relative_path": relative,
                "local_archive_policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
                "network_attempt_count": 0,
                "retry_count": 0,
                "body": b"",
            }
        content_type = _content_type(path, body[:4096])
        if content_type == "application/octet-stream":
            status = "local_archive_unsupported_content_type"
        else:
            status = "fetched"
        return {
            "status": status,
            "http_status": 200,
            "requested_url": canonical,
            "final_url": canonical,
            "redirect_chain": [],
            "content_type": content_type,
            "content_encoding": "",
            "captured_body_bytes": len(body),
            "body_sha256": _sha256_bytes(body),
            "cache_hit": False,
            "local_archive_hit": True,
            "local_archive_relative_path": relative,
            "local_archive_file_sha256": _sha256_bytes(body),
            "local_archive_policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            "retrieval_transport": "operator_managed_local_mirror",
            "network_attempt_count": 0,
            "retry_count": 0,
            "body": body,
        }

    def iter_mtf_document_urls(self) -> Iterable[str]:
        """Yield exact numeric MTF document identities in deterministic order.

        Both an extensionless file and HTTrack's numeric ``.html``
        representation map to the same extensionless public URL.  Conflicting
        dual representations fail closed instead of selecting one silently.
        """
        directory = self.root / "www.margaretthatcher.org" / "document"
        if not directory.is_dir() or directory.is_symlink():
            return
        numbered: dict[str, list[Path]] = defaultdict(list)
        for path in directory.iterdir():
            match = re.fullmatch(r"([0-9]+)(?:\.html)?", path.name)
            if match and path.is_file() and not path.is_symlink():
                numbered[match.group(1)].append(path)
        for name in sorted(numbered, key=int):
            representations = numbered[name]
            if len(representations) > 1:
                hashes = {
                    _sha256_bytes(path.read_bytes()) for path in representations
                }
                if len(hashes) != 1:
                    raise LocalArchiveError(
                        f"conflicting local representations for MTF document {name}"
                    )
            yield f"https://www.margaretthatcher.org/document/{name}"

    def inventory(self) -> LocalArchiveInventory:
        """Hash exact numeric document identities and file metadata without reading bodies."""
        records: list[dict[str, Any]] = []
        total = 0
        for url in self.iter_mtf_document_urls():
            _canonical, path, relative = self._path_for(url)
            stat = path.stat()
            total += int(stat.st_size)
            records.append({
                "relative_path": relative,
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            })
        return LocalArchiveInventory(
            sha256=_sha256_bytes(_canonical_json_bytes(records)),
            document_count=len(records),
            total_bytes=total,
        )

    def configuration(self) -> dict[str, Any]:
        inventory = self.inventory()
        return {
            "enabled": True,
            "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            "root_identity_sha256": self.root_identity_sha256,
            "inventory": inventory.as_dict(),
            "private_root_not_recorded": True,
        }


@dataclass(frozen=True)
class _IndexedDocument:
    url: str
    document_id: str
    title: str
    tokens: tuple[str, ...]
    token_set: frozenset[str]
    file_sha256: str


class LocalMTFDocumentIndex:
    """In-memory inverted index used only to discover canonical MTF URLs."""

    def __init__(
        self,
        mirror: LocalArchiveMirror,
        *,
        maximum_results: int = 50,
    ) -> None:
        self.mirror = mirror
        self.maximum_results = int(maximum_results)
        if self.maximum_results <= 0:
            raise LocalArchiveError("local archive result limit must be positive")
        self.inventory = mirror.inventory()
        documents: list[_IndexedDocument] = []
        postings: dict[str, set[int]] = defaultdict(set)
        for url in mirror.iter_mtf_document_urls():
            record = mirror.read(url)
            if not record or record.get("status") != "fetched":
                continue
            if record.get("content_type") not in {"text/html", "application/xhtml+xml"}:
                continue
            body = bytes(record.get("body") or b"")
            soup = BeautifulSoup(body, "lxml")
            article = max(
                (
                    soup.select_one(".document-body"),
                    soup.select_one("article.node-archive-document"),
                    soup.select_one("#documentbody"),
                ),
                key=lambda node: len(
                    " ".join(node.get_text(" ", strip=True).split())
                ) if node is not None else -1,
            )
            if article is None:
                continue
            text = " ".join(article.get_text(" ", strip=True).split())
            token_list = tuple(_tokens(text))
            if len(token_list) < 20:
                continue
            heading = (
                soup.select_one(".document-header h1")
                or soup.select_one("h1.doctitle")
                or soup.select_one("#documentheader h1")
                or soup.find("h1")
            )
            title = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
            document_id = url.rstrip("/").rsplit("/", 1)[-1]
            index = len(documents)
            document = _IndexedDocument(
                url=url,
                document_id=document_id,
                title=title[:500],
                tokens=token_list,
                token_set=frozenset(token_list),
                file_sha256=str(record.get("local_archive_file_sha256") or ""),
            )
            documents.append(document)
            for token in document.token_set:
                postings[token].add(index)
        self.documents = tuple(documents)
        self.postings = {token: frozenset(values) for token, values in postings.items()}
        self.document_frequency = Counter({
            token: len(values) for token, values in self.postings.items()
        })

    def _selected_anchors(self, phrases: Sequence[str]) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        for phrase_order, phrase in enumerate(phrases):
            for position, token in enumerate(_tokens(phrase)):
                if token in _STOPWORDS or len(token) < 4 or token not in self.postings:
                    continue
                frequency = int(self.document_frequency[token])
                proposal = {
                    "token": token,
                    "document_frequency": frequency,
                    "phrase_order": phrase_order,
                    "token_position": position,
                }
                previous = candidates.get(token)
                if previous is None or (
                    phrase_order, position
                ) < (
                    int(previous["phrase_order"]), int(previous["token_position"])
                ):
                    candidates[token] = proposal
        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                int(item["document_frequency"]),
                -len(str(item["token"])),
                int(item["phrase_order"]),
                int(item["token_position"]),
                str(item["token"]),
            ),
        )
        selected: list[dict[str, Any]] = []
        for candidate in ordered:
            if len(selected) >= 4:
                break
            selected.append(candidate)
        return selected

    def discover(
        self,
        quotation_text: str,
        *,
        variants: Sequence[Mapping[str, Any] | str] = (),
    ) -> dict[str, Any]:
        """Return canonical discovery leads; snippets and bodies are never returned."""
        phrases = [str(quotation_text)]
        variant_diagnostics: list[dict[str, str]] = []
        for item in variants:
            if isinstance(item, Mapping):
                text = str(
                    item.get("variant")
                    or item.get("text")
                    or item.get("wording")
                    or ""
                ).strip()
                provenance = str(item.get("provenance") or item.get("source") or "").strip()
            else:
                text, provenance = str(item).strip(), "authorised_manifest_variant"
            if text and provenance:
                phrases.append(text)
                variant_diagnostics.append({"text_sha256": _sha256_bytes(text.encode()), "provenance": provenance})
        anchors = self._selected_anchors(phrases)
        candidate_ids: set[int] = set()
        if anchors:
            for anchor in anchors:
                candidate_ids.update(self.postings.get(str(anchor["token"]), ()))
        ranked: list[tuple[tuple[Any, ...], _IndexedDocument, dict[str, Any]]] = []
        phrase_tokens = [tuple(_tokens(phrase)) for phrase in phrases if _tokens(phrase)]
        for index in candidate_ids:
            document = self.documents[index]
            matched = [
                anchor for anchor in anchors
                if str(anchor["token"]) in document.token_set
            ]
            exact_phrase_indexes = [
                number for number, tokens in enumerate(phrase_tokens)
                if _contains_contiguous(document.tokens, tokens)
            ]
            if not exact_phrase_indexes and len(matched) < min(2, len(anchors)):
                continue
            rank_key = (
                0 if exact_phrase_indexes else 1,
                -len(exact_phrase_indexes),
                -len(matched),
                int(document.document_id),
            )
            ranked.append((rank_key, document, {
                "matched_anchor_tokens": [str(item["token"]) for item in matched],
                "exact_phrase_indexes": exact_phrase_indexes,
            }))
        ranked.sort(key=lambda item: item[0])
        total = len(ranked)
        results = []
        for rank, (_key, document, diagnostics) in enumerate(
            ranked[:self.maximum_results], 1
        ):
            results.append({
                "result_url": document.url,
                "title": document.title,
                "display_link": "www.margaretthatcher.org",
                "snippet": "",
                "mime": "text/html",
                "document_id": document.document_id,
                "local_archive_rank": rank,
                "local_archive_file_sha256": document.file_sha256,
                **diagnostics,
            })
        return {
            "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            "inventory": self.inventory.as_dict(),
            "indexed_document_count": len(self.documents),
            "selected_anchors": anchors,
            "authorised_variants": variant_diagnostics,
            "candidate_count_before_cap": total,
            "candidate_cap_reached": total > self.maximum_results,
            "results": results,
            "snippets_are_evidence": False,
        }
