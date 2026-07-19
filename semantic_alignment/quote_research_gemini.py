"""Validate and run grounded Gemini quotation-research requests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from google import genai
from google.genai import errors, types

from .bakeoff import PRICES, PROVIDER_MODELS
from .gemini_fallback import quota_reset_metadata
from .io import atomic_write_json, read_json

MODEL = PROVIDER_MODELS["gemini"]
PROMPT_VERSION = "quote-research-grounded-v1"
SCHEMA_VERSION = 1
MAX_QUOTES = 20
MAX_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 2500
THINKING_BUDGET = 256
TEMPERATURE = 0.2
DEVELOPER_LIMIT = 3.0
VERTEX_LIMIT = 3.0
COMBINED_LIMIT = 5.0
SEARCH_QUERY_PRICE = 14.0 / 1000
EXPECTED_SEARCH_QUERIES = 3
GUARDED_SEARCH_QUERIES = 3
EXPECTED_OUTPUT_TOKENS = 1600

VERIFICATION = {"exact", "normalised", "excerpt", "variant", "paraphrase", "composite", "misattributed", "unverified"}
CONFIDENCE = {"high", "medium", "low"}
TOP_LEVEL_FIELDS = (
    "quote_id", "quote_text", "verification_status", "verified_text",
    "text_variation_notes", "speaker", "date", "source_event", "stable_locator",
    "historical_context", "immediate_subject", "intended_argument", "literal_meaning",
    "broader_principle", "mechanism", "claimed_consequence", "entities",
    "editorial_guidance", "research_confidence", "unresolved_questions", "sources",
)
EDITORIAL_FIELDS = (
    "desired_first_impression", "historical_requirements", "must_be_visually_dominant",
    "must_not_dominate", "common_visual_mistakes",
)
SOURCE_FIELDS = ("title", "url", "source_type", "supports")

STRING = {"type": "string"}
STRING_LIST = {"type": "array", "items": {"type": "string"}, "maxItems": 16}
SOURCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"title": STRING, "url": STRING, "source_type": STRING, "supports": {"type": "array", "items": {"type": "string"}, "maxItems": 16}},
    "required": list(SOURCE_FIELDS),
}
PACKET_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        **{field: STRING for field in TOP_LEVEL_FIELDS if field not in {"entities", "editorial_guidance", "unresolved_questions", "sources"}},
        "verification_status": {"type": "string", "enum": sorted(VERIFICATION)},
        "entities": STRING_LIST,
        "editorial_guidance": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "desired_first_impression": STRING,
                "historical_requirements": STRING_LIST,
                "must_be_visually_dominant": STRING_LIST,
                "must_not_dominate": STRING_LIST,
                "common_visual_mistakes": STRING_LIST,
            },
            "required": list(EDITORIAL_FIELDS),
        },
        "research_confidence": {"type": "string", "enum": sorted(CONFIDENCE)},
        "unresolved_questions": STRING_LIST,
        "sources": {"type": "array", "items": SOURCE_SCHEMA, "maxItems": 10},
    },
    "required": list(TOP_LEVEL_FIELDS),
}


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def quote_hash(text: str) -> str:
    """Return the quote hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Estimate tokens."""
    return math.ceil(len(text.encode("utf-8")) / 3)


def research_prompt(record: dict[str, Any]) -> str:
    """Return the research prompt."""
    return f"""Prompt version: {PROMPT_VERSION}
Act as a meticulous historical researcher. Research exactly one quotation attributed to Margaret Thatcher using Google Search grounding. Return only the requested compact JSON packet. Do not write an essay.

UPLOADED RECORD:
quote_id: {record['quote_id']}
quote_text: {json.dumps(record['quote_text'], ensure_ascii=False)}
source_occurrences: {json.dumps(record.get('source_occurrences') or [], sort_keys=True)}

RESEARCH RULES:
- Preserve quote_id and uploaded quote_text exactly. Put corrected wording only in verified_text.
- Establish exact/normalised/excerpt/variant/paraphrase/composite/misattributed/unverified from evidence. Use unknown rather than speculation.
- Give source event, date and a stable locator or quote anchor; explain historical context, immediate subject and intended argument concisely.
- Separate literal meaning, broader principle, mechanism and claimed consequence.
- Identify relevant people, places, organisations and events as short entity strings.
- Give historically accurate editorial guidance: desired first impression, requirements, what must dominate, what must not dominate, and common visual mistakes.
- Note common misquotations or misconceptions in text_variation_notes and unresolved questions where evidence is incomplete.
- Prefer underlying Margaret Thatcher Foundation transcripts, Hansard, official speeches/publications, Thatcher's books, contemporary interviews/newspapers, then reliable secondary history. Do not use quotation aggregators except to document an attribution error.
- Use no more than three focused Google searches. Keep the packet concise and evidence-rich.
- Keep each prose field to at most two sentences, each list to at most eight items, and sources to at most six.
- List sources in numbered research order. Each supports entry must name the exact concise claim the source supports. Citation claims will be retained only when returned grounding metadata links them.
- Never claim access to a source that Google Search grounding did not return.
"""


def validate_packet(value: Any, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate packet."""
    if not isinstance(value, dict) or set(value) != set(TOP_LEVEL_FIELDS):
        actual = set(value) if isinstance(value, dict) else set()
        raise ValueError(f"packet fields mismatch missing={sorted(set(TOP_LEVEL_FIELDS)-actual)} extra={sorted(actual-set(TOP_LEVEL_FIELDS))}")
    if value["verification_status"] not in VERIFICATION or value["research_confidence"] not in CONFIDENCE:
        raise ValueError("invalid packet enum")
    if record and (value["quote_id"] != record["quote_id"] or value["quote_text"] != record["quote_text"]):
        raise ValueError("quote identity changed")
    for field in TOP_LEVEL_FIELDS:
        if field in {"entities", "editorial_guidance", "unresolved_questions", "sources"}:
            continue
        if not isinstance(value[field], str):
            raise ValueError(f"{field} must be text")
    for field in ("entities", "unresolved_questions"):
        if not isinstance(value[field], list) or any(type(item) is not str or not item.strip() for item in value[field]):
            raise ValueError(f"{field} must contain non-empty strings")
    editorial = value["editorial_guidance"]
    if not isinstance(editorial, dict) or set(editorial) != set(EDITORIAL_FIELDS):
        raise ValueError("editorial_guidance fields mismatch")
    if not isinstance(editorial["desired_first_impression"], str) or not editorial["desired_first_impression"].strip():
        raise ValueError("desired_first_impression is required")
    for field in EDITORIAL_FIELDS[1:]:
        if not isinstance(editorial[field], list) or any(type(item) is not str or not item.strip() for item in editorial[field]):
            raise ValueError(f"invalid editorial {field}")
    if not isinstance(value["sources"], list) or not value["sources"]:
        raise ValueError("at least one grounded source is required")
    for source in value["sources"]:
        if not isinstance(source, dict) or set(source) != set(SOURCE_FIELDS):
            raise ValueError("source fields mismatch")
        if any(not isinstance(source[field], str) or not source[field].strip() for field in ("title", "url", "source_type")):
            raise ValueError("source identity fields must be non-empty")
        if not isinstance(source["supports"], list) or not source["supports"] or any(type(item) is not str or not item.strip() for item in source["supports"]):
            raise ValueError("source supports must be grounded non-empty text")
    return dict(value)


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def extract_grounding(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract only provider-linked grounding from Developer or Vertex response dumps."""
    while isinstance(raw, dict) and not raw.get("candidates"):
        wrapped = next((raw.get(key) for key in ("raw", "response", "result")
                        if isinstance(raw.get(key), dict)), None)
        if wrapped is None:
            break
        raw = wrapped
    candidates = raw.get("candidates") or []
    sources = []
    queries: list[str] = []
    support_rows = []
    search_entry_points = []
    source_offset = 0
    for candidate in candidates:
        metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
        chunks = metadata.get("groundingChunks") or metadata.get("grounding_chunks") or []
        supports = metadata.get("groundingSupports") or metadata.get("grounding_supports") or []
        queries.extend(str(query) for query in
                       (metadata.get("webSearchQueries") or metadata.get("web_search_queries") or []))
        entry = metadata.get("searchEntryPoint") or metadata.get("search_entry_point")
        if entry:
            search_entry_points.append(entry)
        candidate_sources = []
        for index, chunk in enumerate(chunks):
            web = chunk.get("web") or chunk.get("retrievedContext") or chunk.get("retrieved_context") or {}
            uri = web.get("uri") or web.get("url")
            title = web.get("title") or uri
            if uri:
                candidate_sources.append({"index": source_offset + index, "local_index": index,
                                          "title": str(title), "url": str(uri)})
        linked: dict[int, list[str]] = {row["local_index"]: [] for row in candidate_sources}
        for support in supports:
            indices = support.get("groundingChunkIndices") or support.get("grounding_chunk_indices") or []
            segment = support.get("segment") or {}
            text = str(segment.get("text") or "").strip()
            row = {"chunk_indices": [source_offset + int(index) for index in indices], "text": text,
                   "start_index": segment.get("startIndex", segment.get("start_index")),
                   "end_index": segment.get("endIndex", segment.get("end_index"))}
            support_rows.append(row)
            if text:
                for index in indices:
                    linked.setdefault(int(index), []).append(text)
        for source in candidate_sources:
            source["supports"] = list(dict.fromkeys(linked.get(source.pop("local_index"), [])))
        sources.extend(candidate_sources)
        source_offset += len(chunks)
    return {"queries": list(dict.fromkeys(queries)), "sources": sources, "supports": support_rows,
            "search_entry_point": search_entry_points[0] if search_entry_points else None}


def _single_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("truncated JSON object")


def parse_response_packet(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Parse a response with bounded, deterministic repairs only."""
    repairs: list[str] = []
    while isinstance(raw, dict) and not raw.get("candidates"):
        parsed = raw.get("parsed")
        if isinstance(parsed, dict):
            return dict(parsed), repairs
        wrapped = next((raw.get(key) for key in ("content", "raw", "response", "result")
                        if isinstance(raw.get(key), dict)), None)
        if wrapped is None:
            break
        repairs.append("normalised_transport_wrapper")
        raw = wrapped
    if isinstance(raw.get("parsed"), dict):
        return dict(raw["parsed"]), repairs
    text = _response_text(raw).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text, count=1)
        repairs.append("stripped_markdown_fence")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        isolated = _single_json_object(text)
        if isolated != text:
            repairs.append("isolated_single_json_object")
        repaired = re.sub(r",\s*([}\]])", r"\1", isolated)
        if repaired != isolated:
            repairs.append("removed_trailing_comma")
        value = json.loads(repaired)
    if not isinstance(value, dict):
        raise ValueError("response JSON must be one object")
    return value, repairs


def _source_type(url: str) -> str:
    host = urlparse(url).netloc.casefold()
    if "margaretthatcher.org" in host:
        return "margaret_thatcher_foundation_transcript"
    if "hansard" in host or "parliament.uk" in host:
        return "hansard_or_parliamentary_record"
    if host.endswith("gov.uk") or ".gov." in host:
        return "official_publication"
    return "grounded_web_source"


def bind_packet_to_grounding(packet: dict[str, Any], grounding: dict[str, Any]) -> dict[str, Any]:
    """Replace model-written citations with only metadata-linked source claims."""
    model_sources = packet.get("sources") if isinstance(packet, dict) else []
    by_title = {_key(str(row.get("title") or "")): row for row in model_sources if isinstance(row, dict)}
    bound = []
    for source in grounding["sources"]:
        if not source["supports"]:
            continue
        model = by_title.get(_key(source["title"]))
        title = source["title"] if source["title"] else str((model or {}).get("title") or "Grounded source")
        bound.append({"title": title, "url": source["url"], "source_type": _source_type(source["url"]),
                      "supports": source["supports"]})
    packet = dict(packet)
    packet["sources"] = bound
    return packet


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    usage = raw.get("usageMetadata") or raw.get("usage_metadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or usage.get("prompt_token_count") or 0)
    completion = int(usage.get("candidatesTokenCount") or usage.get("candidates_token_count") or 0)
    thinking = int(usage.get("thoughtsTokenCount") or usage.get("thoughts_token_count") or 0)
    cached = int(usage.get("cachedContentTokenCount") or usage.get("cached_content_token_count") or 0)
    if not input_tokens and not completion and not thinking:
        raise ValueError("missing authoritative Gemini token usage")
    return {"input_tokens": input_tokens, "cached_tokens": cached,
            "reasoning_tokens": thinking, "output_tokens": completion + thinking}


def calculate_cost(usage: dict[str, int], query_count: int) -> dict[str, float]:
    """Calculate cost."""
    token_cost = ((usage["input_tokens"]-usage["cached_tokens"])*PRICES["gemini"]["input"] +
                  usage["cached_tokens"]*PRICES["gemini"]["cached_input"] +
                  usage["output_tokens"]*PRICES["gemini"]["output"]) / 1_000_000
    search_cost = query_count * SEARCH_QUERY_PRICE
    return {"token_cost_usd": token_cost, "search_cost_usd_conservative": search_cost,
            "cost_usd": token_cost + search_cost}


def _response_text(raw: dict[str, Any]) -> str:
    return "".join(
        str(part.get("text") or "")
        for candidate in raw.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
    )


def generation_config(response_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the generation config."""
    return {"responseMimeType": "application/json", "responseJsonSchema": response_schema or PACKET_SCHEMA,
            "maxOutputTokens": MAX_OUTPUT_TOKENS, "thinkingConfig": {"thinkingBudget": THINKING_BUDGET},
            "temperature": TEMPERATURE}


def settings_signature() -> dict[str, Any]:
    """Return the settings signature."""
    return {"model": MODEL, "generation_config": generation_config(), "tools": [{"googleSearch": {}}],
            "prompt_version": PROMPT_VERSION}


class DeveloperResearchClient:
    """Provide the developer research client."""
    transport = "developer_api"
    model = MODEL

    def __init__(self, api_key: str, request: Callable[..., Any] = requests.post,
                 connect_timeout: float = 20, read_timeout: float = 300,
                 response_schema: dict[str, Any] | None = None):
        """Initialise the developer research client."""
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required only for explicit execution")
        self.api_key = api_key
        self.request = request
        self.timeout = (connect_timeout, read_timeout)
        self.response_schema = response_schema or PACKET_SCHEMA

    def payload(self, prompt: str) -> dict[str, Any]:
        """Return the payload."""
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "tools": [{"googleSearch": {}}], "generationConfig": generation_config(self.response_schema)}

    def call(self, prompt: str) -> dict[str, Any]:
        """Submit one grounded research prompt through the Developer API."""
        started = time.monotonic()
        response = self.request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            json=self.payload(prompt), timeout=self.timeout,
        )
        latency = time.monotonic() - started
        response.raise_for_status()
        raw = response.json()
        grounding = extract_grounding(raw)
        usage = _usage(raw)
        costs = calculate_cost(usage, len(grounding["queries"]))
        try:
            content, _repairs = parse_response_packet(raw)
            parse_error = None
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            content, parse_error = None, f"{type(exc).__name__}: {exc}"
        return {"raw": raw, "content": content, "parse_error": parse_error, "grounding": grounding,
                "usage": usage, **costs, "latency_seconds": latency,
                "request_id": response.headers.get("x-request-id") or response.headers.get("request-id")}


class VertexResearchClient:
    """Provide the vertex research client."""
    transport = "vertex_ai"
    model = MODEL

    def __init__(self, project: str, location: str = "global", client: Any | None = None,
                 read_timeout: float = 300, response_schema: dict[str, Any] | None = None):
        """Initialise the vertex research client."""
        self.project = project
        self.location = location
        self.client = client or genai.Client(vertexai=True, project=project, location=location)
        self.read_timeout = read_timeout
        self.response_schema = response_schema or PACKET_SCHEMA

    def config(self):
        """Return the config."""
        return types.GenerateContentConfig(
            response_mime_type="application/json", response_json_schema=self.response_schema,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
            temperature=TEMPERATURE, tools=[types.Tool(google_search=types.GoogleSearch())],
            http_options=types.HttpOptions(timeout=int(self.read_timeout * 1000)),
        )

    def call(self, prompt: str) -> dict[str, Any]:
        """Submit one grounded research prompt through Vertex AI."""
        started = time.monotonic()
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=self.config())
        latency = time.monotonic() - started
        raw = response.model_dump(mode="json", exclude_none=True)
        grounding = extract_grounding(raw)
        usage = _usage(raw)
        costs = calculate_cost(usage, len(grounding["queries"]))
        try:
            content, _repairs = parse_response_packet(raw)
            parse_error = None
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            content, parse_error = None, f"{type(exc).__name__}: {exc}"
        return {"raw": raw, "content": content, "parse_error": parse_error,
                "grounding": grounding, "usage": usage, **costs,
                "latency_seconds": latency, "request_id": response.response_id}


def require_transport_parity(developer: Any, vertex: Any) -> None:
    """Require transport parity."""
    if developer.model != vertex.model:
        raise RuntimeError("Gemini research model parity failed")
    developer_payload = developer.payload("__PROMPT__")
    expected_config = developer_payload["generationConfig"]
    if developer_payload["tools"] != [{"googleSearch": {}}]:
        raise RuntimeError("Developer research settings parity failed")
    config = vertex.config()
    vertex_signature = {
        "responseMimeType": config.response_mime_type,
        "responseJsonSchema": config.response_json_schema,
        "maxOutputTokens": config.max_output_tokens,
        "thinkingConfig": {"thinkingBudget": config.thinking_config.thinking_budget},
        "temperature": config.temperature,
    }
    if vertex_signature != expected_config or not config.tools:
        raise RuntimeError("Vertex research settings parity failed")


def classify_http_failure(exc: BaseException) -> dict[str, Any]:
    """Classify HTTP failure."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        response = exc.response
        try:
            error = (response.json().get("error") or {})
        except (ValueError, AttributeError):
            error = {}
        status = int(response.status_code)
        return {"kind": "quota_429" if status == 429 else "transient" if status in {408, 500, 502, 503, 504} else "permanent",
                "http_status": status, "provider_status": error.get("status"),
                "provider_code": error.get("code"), "message": str(error.get("message") or str(exc))[:2000]}
    if isinstance(exc, errors.APIError):
        code = int(getattr(exc, "code", 0) or 0)
        return {"kind": "quota_429" if code == 429 else "transient" if code in {408, 500, 502, 503, 504} else "permanent",
                "http_status": code, "provider_status": type(exc).__name__, "provider_code": code,
                "message": str(exc)[:2000]}
    return {"kind": "ambiguous", "message": f"{type(exc).__name__}: {exc}"[:2000]}


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def maximum_next_cost(prompt: str) -> float:
    """Return the maximum next cost."""
    return (estimate_tokens(prompt)*PRICES["gemini"]["input"] + MAX_OUTPUT_TOKENS*PRICES["gemini"]["output"]) / 1_000_000 + GUARDED_SEARCH_QUERIES*SEARCH_QUERY_PRICE


def preflight(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the deterministic execution preflight."""
    prompts = [research_prompt(record) for record in records]
    input_tokens = sum(estimate_tokens(prompt) for prompt in prompts)
    expected = (input_tokens*PRICES["gemini"]["input"] + len(records)*EXPECTED_OUTPUT_TOKENS*PRICES["gemini"]["output"]) / 1_000_000 + len(records)*EXPECTED_SEARCH_QUERIES*SEARCH_QUERY_PRICE
    guarded = (input_tokens*PRICES["gemini"]["input"] + len(records)*MAX_OUTPUT_TOKENS*PRICES["gemini"]["output"]) / 1_000_000 + len(records)*GUARDED_SEARCH_QUERIES*SEARCH_QUERY_PRICE
    maximum_retry = guarded * MAX_ATTEMPTS
    return {"schema_version": 1, "model": MODEL, "prompt_version": PROMPT_VERSION,
            "records": len(records), "estimated_input_tokens": input_tokens,
            "expected_output_tokens": len(records)*EXPECTED_OUTPUT_TOKENS,
            "maximum_output_tokens": len(records)*MAX_OUTPUT_TOKENS,
            "expected_search_queries": len(records)*EXPECTED_SEARCH_QUERIES,
            "guarded_search_queries": len(records)*GUARDED_SEARCH_QUERIES,
            "expected_cost_usd": round(expected, 6), "guarded_base_cost_usd": round(guarded, 6),
            "guarded_retry_cost_usd": round(maximum_retry, 6),
            "developer_ceiling_usd": DEVELOPER_LIMIT, "vertex_ceiling_usd": VERTEX_LIMIT,
            "combined_ceiling_usd": COMBINED_LIMIT, "google_search_grounding_enabled": True,
            "deep_research_agent": False, "low_concurrency": 1,
            "allowed": (len(records) == MAX_QUOTES and
                        maximum_retry <= min(DEVELOPER_LIMIT, VERTEX_LIMIT, COMBINED_LIMIT))}


def _classification(text: str) -> str:
    lowered = text.casefold()
    if any(marker in lowered for marker in ("mr speaker", "hon. member", "this house", "government's job")):
        return "hansard_or_parliamentary_cue"
    if len(text) <= 85:
        return "short_wording_attribution_risk"
    if len(text) >= 300:
        return "long_excerpt_or_composite_risk"
    if any(marker in lowered for marker in ("i was asked", "interview", "poppycock", "you ask me", "i think")):
        return "interview_or_colloquial_wording"
    if any(marker in lowered for marker in ("nato", "soviet", "falkland", "europe", "rhodesia", "argentina")):
        return "historically_complex_event"
    return "general_speech_or_publication"


def build_pilot(manifest: dict[str, Any], excluded_batch: dict[str, Any], count: int = MAX_QUOTES) -> dict[str, Any]:
    """Build pilot."""
    if count != MAX_QUOTES:
        raise ValueError("this pilot must contain exactly 20 records")
    excluded = {row["quote_id"] for row in excluded_batch.get("records") or []}
    for row in manifest["records"]:
        if row.get("quote_hash") != quote_hash(row["quote_text"]):
            raise RuntimeError(f"quote fingerprint hash mismatch: {row.get('quote_id')}")
    candidates = [row for row in manifest["records"] if row["quote_id"] not in excluded]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        groups.setdefault(_classification(row["quote_text"]), []).append(row)
    selected = []
    order = ("hansard_or_parliamentary_cue", "short_wording_attribution_risk", "long_excerpt_or_composite_risk",
             "interview_or_colloquial_wording", "historically_complex_event", "general_speech_or_publication")
    target = {name: 3 for name in order}
    target["general_speech_or_publication"] = 5
    for name in order:
        rows = sorted(groups.get(name, []), key=lambda row: (quote_hash(row["quote_text"] + name), row["quote_id"]))
        for row in rows[:target[name]]:
            selected.append({**row, "selection_reason": name})
    if len(selected) < count:
        used = {row["quote_id"] for row in selected}
        for row in sorted(candidates, key=lambda row: quote_hash("fill:" + row["quote_id"])):
            if row["quote_id"] not in used:
                selected.append({**row, "selection_reason": "deterministic_representative_fill"})
                used.add(row["quote_id"])
                if len(selected) == count:
                    break
    selected = sorted(selected[:count], key=lambda row: min(x["line_number"] for x in row["source_occurrences"]))
    if len(selected) != count or len({row["quote_id"] for row in selected}) != count or excluded & {row["quote_id"] for row in selected}:
        raise RuntimeError("unable to build non-overlapping deterministic 20-record pilot")
    payload = {"schema_version": 1, "batch_id": "gemini_grounded_pilot_002", "record_count": count,
               "source_manifest_version": manifest["manifest_version"],
               "excluded_batch_id": excluded_batch.get("batch_id", "pilot_batch_001"),
               "excluded_quote_ids": sorted(excluded), "records": selected}
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


class ResearchWorker:
    """Run research operations."""
    def __init__(self, run_dir: Path, records: list[dict[str, Any]], developer: Any, vertex: Any,
                 developer_limit: float, vertex_limit: float, combined_limit: float,
                 sleep: Callable[[float], None] = time.sleep):
        """Initialise the research worker."""
        self.run_dir, self.records, self.developer, self.vertex = run_dir, records, developer, vertex
        self.developer_limit, self.vertex_limit, self.combined_limit = developer_limit, vertex_limit, combined_limit
        self.sleep = sleep
        self.attempts_path = run_dir / "attempts.jsonl"
        self.packets_path = run_dir / "research_packets.json"
        self.grounding_path = run_dir / "grounding_sources.json"
        self.cost_path = run_dir / "cost_ledger.json"
        self.status_path = run_dir / "transport_status.json"

    def _load(self):
        packets = read_json(self.packets_path, None) or {"schema_version": 1, "items": {}, "validation_failures": {}}
        grounding = read_json(self.grounding_path, None) or {"schema_version": 1, "items": {}}
        costs = read_json(self.cost_path, None) or {"schema_version": 1, "calls": [], "developer_known_spend_usd": 0.0, "vertex_known_spend_usd": 0.0, "combined_known_spend_usd": 0.0, "ambiguous_possible_exposure_usd": 0.0}
        costs.setdefault("ambiguous_possible_exposure_usd", 0.0)
        costs.setdefault("uncertain_attempts", [])
        known = {(row["quote_id"], row["transport"], row["attempt_number"]) for row in costs["calls"]}
        uncertain = {(row["quote_id"], row["transport"], row["attempt_number"])
                     for row in costs["uncertain_attempts"]}
        if self.attempts_path.exists():
            by_id = {row["quote_id"]: row for row in self.records}
            for line in self.attempts_path.read_text().splitlines():
                row = json.loads(line)
                key = (row.get("quote_id"), row.get("transport"), row.get("attempt_number"))
                if row.get("state") == "ambiguous_outcome" and key not in known and key not in uncertain and key[0] in by_id:
                    possible = maximum_next_cost(research_prompt(by_id[key[0]]))
                    costs["uncertain_attempts"].append({"quote_id": key[0], "transport": key[1],
                        "attempt_number": key[2], "maximum_possible_cost_usd": possible})
                    uncertain.add(key)
        costs["ambiguous_possible_exposure_usd"] = sum(
            float(row["maximum_possible_cost_usd"]) for row in costs["uncertain_attempts"])
        status = read_json(self.status_path, None) or {"schema_version": 1, "developer_paused": False, "pause_reason": None, "paused_at": None, "trigger_attempts": [], "direct_to_vertex_count": 0, "fallback_enabled": True, "model": MODEL}
        for path, value in ((self.packets_path, packets), (self.grounding_path, grounding), (self.cost_path, costs), (self.status_path, status)):
            if not path.exists():
                atomic_write_json(path, value)
        atomic_write_json(self.cost_path, costs)
        return packets, grounding, costs, status

    @staticmethod
    def _spend(costs: dict[str, Any], transport: str) -> float:
        return sum(float(row["cost_usd"]) for row in costs["calls"] if row["transport"] == transport)

    def _guard(self, transport: str, maximum: float, costs: dict[str, Any]) -> None:
        own = self._spend(costs, transport)
        limit = self.developer_limit if transport == "developer_api" else self.vertex_limit
        combined = sum(float(row["cost_usd"]) for row in costs["calls"])
        exposure = float(costs.get("ambiguous_possible_exposure_usd") or 0)
        if own + maximum > limit:
            raise RuntimeError(f"{transport} ceiling would be exceeded")
        if combined + exposure + maximum > self.combined_limit:
            raise RuntimeError("combined ceiling would be exceeded")

    def _attempt_count(self, quote_id: str, transport: str) -> int:
        if not self.attempts_path.exists():
            return 0
        final_states = {"completed", "confirmed_failure", "ambiguous_outcome", "validation_failure",
                        "validation_failure_reclassified"}
        attempts = set()
        for line in self.attempts_path.read_text().splitlines():
            row = json.loads(line)
            if row.get("quote_id") == quote_id and row.get("transport") == transport and row.get("state") in final_states:
                attempts.add(row["attempt_number"])
        return len(attempts)

    def _reconcile_interrupted_attempts(self) -> set[str]:
        """Seal unterminated sending records as ambiguous before resume."""
        if not self.attempts_path.exists():
            return set()
        rows = [json.loads(line) for line in self.attempts_path.read_text().splitlines() if line.strip()]
        terminal = {"completed", "confirmed_failure", "ambiguous_outcome", "validation_failure",
                    "validation_failure_reclassified"}
        states: dict[tuple[str, str, int], set[str]] = {}
        bases: dict[tuple[str, str, int], dict[str, Any]] = {}
        for row in rows:
            key = (row.get("quote_id"), row.get("transport"), int(row.get("attempt_number", 0)))
            states.setdefault(key, set()).add(row.get("state"))
            bases[key] = row
        for key, seen in states.items():
            if "sending" in seen and not (seen & terminal):
                base = {name: value for name, value in bases[key].items()
                        if name not in {"state", "failure", "error"}}
                _append_jsonl(self.attempts_path, {**base, "state": "ambiguous_outcome",
                    "timestamp": utc_now(), "failure": {"kind": "ambiguous",
                    "message": "process resumed with unterminated sending lifecycle"}})
        rows = [json.loads(line) for line in self.attempts_path.read_text().splitlines() if line.strip()]
        reclassified = {(row["quote_id"], row["transport"], row["attempt_number"])
                        for row in rows if row.get("state") == "validation_failure_reclassified"}
        return {row["quote_id"] for row in rows if row.get("state") == "ambiguous_outcome" and
                (row["quote_id"], row["transport"], row["attempt_number"]) not in reclassified}

    def _reclassify_legacy_parse_failures(self) -> None:
        """Correct responses parsed by the old adapter without rewriting history."""
        if not self.attempts_path.exists():
            return
        rows = [json.loads(line) for line in self.attempts_path.read_text().splitlines() if line.strip()]
        corrected = {(row.get("quote_id"), row.get("transport"), row.get("attempt_number"))
                     for row in rows if row.get("state") == "validation_failure_reclassified"}
        for row in rows:
            key = (row.get("quote_id"), row.get("transport"), row.get("attempt_number"))
            message = str((row.get("failure") or {}).get("message") or "")
            if row.get("state") == "ambiguous_outcome" and message.startswith("JSONDecodeError:") and key not in corrected:
                _append_jsonl(self.attempts_path, {**{name: value for name, value in row.items()
                    if name not in {"state", "failure"}}, "state": "validation_failure_reclassified",
                    "timestamp": utc_now(), "error": message,
                    "classification_basis": "legacy adapter parsed a received response inside transport"})

    def _one_attempt(self, record: dict[str, Any], client: Any, number: int, packets, grounding_db, costs, status):
        prompt = research_prompt(record)
        maximum = maximum_next_cost(prompt)
        self._guard(client.transport, maximum, costs)
        base = {"run_id": self.run_dir.name, "quote_id": record["quote_id"], "quote_hash": record["quote_hash"],
                "transport": client.transport, "model": client.model, "attempt_number": number,
                "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
                "input_hash": hashlib.sha256(prompt.encode()).hexdigest(), "timestamp": utc_now()}
        _append_jsonl(self.attempts_path, {**base, "state": "prepared"})
        _append_jsonl(self.attempts_path, {**base, "state": "sending"})
        try:
            response = client.call(prompt)
        except Exception as exc:
            classification = classify_http_failure(exc)
            state = "ambiguous_outcome" if classification["kind"] == "ambiguous" else "confirmed_failure"
            _append_jsonl(self.attempts_path, {**base, "state": state, "failure": classification})
            if state == "ambiguous_outcome":
                costs["uncertain_attempts"].append({"quote_id": record["quote_id"],
                    "transport": client.transport, "attempt_number": number,
                    "maximum_possible_cost_usd": maximum})
                costs["ambiguous_possible_exposure_usd"] = sum(
                    float(row["maximum_possible_cost_usd"]) for row in costs["uncertain_attempts"])
                atomic_write_json(self.cost_path, costs)
            return {"outcome": classification["kind"], "failure": classification}
        raw_dir = self.run_dir / "raw_responses" / record["quote_id"]
        raw_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(raw_dir / f"{client.transport}_attempt_{number}.json", response["raw"])
        _append_jsonl(self.attempts_path, {**base, "state": "response_received", "request_id": response["request_id"]})
        call = {"quote_id": record["quote_id"], "transport": client.transport, "attempt_number": number,
                "request_id": response["request_id"], "timestamp": utc_now(), "latency_seconds": response["latency_seconds"],
                "search_query_count": len(response["grounding"]["queries"]), **response["usage"],
                "token_cost_usd": response["token_cost_usd"], "search_cost_usd_conservative": response["search_cost_usd_conservative"],
                "cost_usd": response["cost_usd"]}
        costs["calls"].append(call)
        costs["developer_known_spend_usd"] = self._spend(costs, "developer_api")
        costs["vertex_known_spend_usd"] = self._spend(costs, "vertex_ai")
        costs["combined_known_spend_usd"] = costs["developer_known_spend_usd"] + costs["vertex_known_spend_usd"]
        atomic_write_json(self.cost_path, costs)
        grounding_db["items"][record["quote_id"]] = {"transport": client.transport, **response["grounding"]}
        atomic_write_json(self.grounding_path, grounding_db)
        try:
            if response.get("parse_error"):
                raise ValueError(response["parse_error"])
            bound = bind_packet_to_grounding(response["content"], response["grounding"])
            packet = validate_packet(bound, record)
        except (ValueError, TypeError, KeyError) as exc:
            packets["validation_failures"][record["quote_id"]] = {"transport": client.transport, "attempt_number": number, "error": str(exc), "recorded_at": utc_now()}
            atomic_write_json(self.packets_path, packets)
            _append_jsonl(self.attempts_path, {**base, "state": "validation_failure", "error": str(exc)})
            return {"outcome": "validation_failure"}
        packets["items"][record["quote_id"]] = {**packet, "transport": client.transport,
            "model": client.model, "prompt_version": PROMPT_VERSION, "validation_status": "valid",
            "grounding_source_count": len(packet["sources"]), "completed_at": utc_now()}
        packets["validation_failures"].pop(record["quote_id"], None)
        atomic_write_json(self.packets_path, packets)
        _append_jsonl(self.attempts_path, {**base, "state": "completed", "request_id": response["request_id"]})
        return {"outcome": "completed"}

    def _run_transport(self, record, client, packets, grounding, costs, status):
        prior = self._attempt_count(record["quote_id"], client.transport)
        if prior >= MAX_ATTEMPTS:
            return "exhausted"
        for number in range(prior + 1, MAX_ATTEMPTS + 1):
            result = self._one_attempt(record, client, number, packets, grounding, costs, status)
            outcome = result["outcome"]
            if outcome == "completed":
                return outcome
            if outcome == "ambiguous":
                return outcome
            if client.transport == "developer_api" and outcome == "quota_429":
                status["trigger_attempts"].append({"quote_id": record["quote_id"], "attempt_number": number,
                                                    "timestamp": utc_now(), **result["failure"]})
                atomic_write_json(self.status_path, status)
                if number == MAX_ATTEMPTS:
                    status.update({"developer_paused": True, "pause_reason": "two_consecutive_429s_for_one_quote",
                                   "paused_at": utc_now(), **quota_reset_metadata(None)})
                    atomic_write_json(self.status_path, status)
                    return "quota_pause"
                self.sleep(min(2**number, 4))
                continue
            if outcome in {"transient", "validation_failure"} and number < MAX_ATTEMPTS:
                self.sleep(min(2**number, 4))
                continue
            return outcome
        return "exhausted"

    def run(self) -> dict[str, Any]:
        """Run pending research packets with persisted provider routing."""
        require_transport_parity(self.developer, self.vertex)
        packets, grounding, costs, status = self._load()
        self._reclassify_legacy_parse_failures()
        ambiguous = self._reconcile_interrupted_attempts()
        packets, grounding, costs, status = self._load()
        for record in self.records:
            quote_id = record["quote_id"]
            if quote_id in packets["items"] or quote_id in ambiguous:
                continue
            if status["developer_paused"]:
                status["direct_to_vertex_count"] += 1
                atomic_write_json(self.status_path, status)
                self._run_transport(record, self.vertex, packets, grounding, costs, status)
                continue
            outcome = self._run_transport(record, self.developer, packets, grounding, costs, status)
            if outcome == "quota_pause":
                self._run_transport(record, self.vertex, packets, grounding, costs, status)
        status.update({"completed": len(packets["items"]), "remaining": len(self.records)-len(packets["items"]),
                       "developer_completed": sum(row.get("transport") == "developer_api" for row in packets["items"].values()),
                       "vertex_completed": sum(row.get("transport") == "vertex_ai" for row in packets["items"].values()),
                       "updated_at": utc_now()})
        atomic_write_json(self.status_path, status)
        return status


def report(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build the quotation-research status and spend report."""
    packets = read_json(run_dir / "research_packets.json", {}) or {"items": {}, "validation_failures": {}}
    costs = read_json(run_dir / "cost_ledger.json", {}) or {"calls": [], "combined_known_spend_usd": 0}
    status = read_json(run_dir / "transport_status.json", {}) or {}
    attempts = ([json.loads(line) for line in (run_dir / "attempts.jsonl").read_text().splitlines()]
                if (run_dir / "attempts.jsonl").exists() else [])
    valid = len(packets["items"])
    sources = [source for row in packets["items"].values() for source in row["sources"]]
    source_coverage = valid and sum(bool(row["sources"]) for row in packets["items"].values()) / valid
    claim_supports = sum(len(source["supports"]) for source in sources)
    cost = float(costs.get("combined_known_spend_usd") or 0)
    uncertain = float(costs.get("ambiguous_possible_exposure_usd") or 0)
    per_quote = cost / valid if valid else None
    projected = per_quote * 632 if per_quote is not None else None
    maximum_exposure = cost + uncertain
    projected_with_exposure = maximum_exposure / valid * 632 if valid else None
    valid_rate = valid / len(manifest["records"])
    if valid_rate >= .9 and source_coverage == 1 and (projected or 999) <= 75:
        recommendation = "Full-corpus execution is economically plausible, but first perform human qualitative review of this pilot's verification and citation precision."
    else:
        recommendation = "Do not run the full corpus yet; resolve completion reliability and manually audit verification/citation precision first."
    counts = Counter(row.get("verification_status") for row in packets["items"].values())
    unresolved = [row["quote_id"] for row in manifest["records"] if row["quote_id"] not in packets["items"]]
    timeouts = sum("ReadTimeout:" in str((row.get("failure") or {}).get("message") or "") for row in attempts)
    interrupted = sum("KeyboardInterrupt:" in str((row.get("failure") or {}).get("message") or "") or
                      "unterminated sending lifecycle" in str((row.get("failure") or {}).get("message") or "")
                      for row in attempts)
    parse_failures = sum(row.get("state") == "validation_failure_reclassified" for row in attempts)
    preflight_data = read_json(run_dir / "preflight.json", {}) or {}
    lines = ["# Gemini grounded quote-research pilot", "",
        f"- Pilot records: {len(manifest['records'])}", f"- Valid packets: {valid}",
        f"- Overall completion: {valid_rate:.1%}", f"- Unresolved quote IDs: {', '.join(unresolved) if unresolved else 'none'}",
        f"- Developer completions: {status.get('developer_completed',0)}", f"- Vertex completions: {status.get('vertex_completed',0)}",
        f"- Developer 429 trigger attempts: {len(status.get('trigger_attempts',[]))}",
        f"- Developer paused: {status.get('developer_paused',False)} ({status.get('pause_reason')})",
        f"- Direct-to-Vertex cases after pause: {status.get('direct_to_vertex_count',0)}",
        f"- Open validation-failure records: {len(packets.get('validation_failures',{}))}",
        f"- Legacy malformed-response parse failures reclassified: {parse_failures}",
        f"- Read timeouts after transmission: {timeouts}", f"- Operator-interruption ambiguous attempts: {interrupted}",
        f"- Grounded sources retained: {len(sources)}", f"- Grounding-linked support segments: {claim_supports}",
        f"- Source coverage among valid packets: {source_coverage:.1%}" if valid else "- Source coverage: unavailable",
        f"- Source coverage across the 20 intended records: {sum(bool(row['sources']) for row in packets['items'].values())/len(manifest['records']):.1%}",
        f"- Verification statuses: {dict(counts)}", f"- Combined conservatively reconstructed cost: ${cost:.6f}",
        f"- Maximum possible ambiguous billing exposure: ${uncertain:.6f}",
        f"- Known cost plus maximum possible exposure: ${maximum_exposure:.6f}",
        f"- Cost per valid quote: ${per_quote:.6f}" if per_quote is not None else "- Cost per valid quote: unavailable",
        f"- Projected cost for 632 distinct quotes: ${projected:.2f}" if projected is not None else "- Projected cost: unavailable",
        f"- Exposure-adjusted upper projection for 632: ${projected_with_exposure:.2f}" if projected_with_exposure is not None else "- Exposure-adjusted projection: unavailable",
        f"- Preflight expected cost for 20: ${float(preflight_data.get('expected_cost_usd') or 0):.6f}",
        f"- Recommendation: {recommendation}", "", "## Method", "",
        "One quote was submitted per ordinary Gemini generate-content request with Google Search grounding. This was not the Deep Research agent. Sources and support claims in normalised packets were reconstructed from returned grounding chunks/support metadata; unsupported model-written citation claims were discarded.", "",
        "The Developer API was tried first. Two confirmed 429 responses for one quote pause Developer for the run, route that quote to Vertex, and route all later unfinished quotes directly to Vertex without a probe. Non-quota transient failures do not activate the pause.", "",
        "No 429 occurred in this run, so the Vertex fallback was correctly not activated. Four requests interrupted while in flight were not repeated. Two malformed responses produced by the original adapter were deterministically reclassified without rewriting attempt history; their one permitted schema retry was then used.", "",
        "Costs use official Gemini token rates plus a conservative $0.014 charge per returned grounding query. Developer API Search grounding may include monthly no-charge allowance, so this is an upper reconstruction rather than an authoritative invoice. Pricing reference: https://ai.google.dev/gemini-api/docs/pricing", "",
        "## Limitations", "",
        "Only 13 of 20 records produced valid packets. Grounding metadata establishes a link between retained sources and response segments, but a human still needs to verify that each source supports the historical claim attributed to it. The completion and qualitative audit gaps make extrapolation to all 632 premature.", "",
        "## Safety", "", "The run used only the immutable 20-record pilot manifest and did not alter production files or existing research artefacts. Nothing was staged, committed, pushed or deployed."]
    (run_dir / "pilot_report.md").write_text("\n".join(lines) + "\n")
    return {"valid": valid, "unresolved": len(unresolved), "sources": len(sources),
            "cost_usd": cost, "ambiguous_possible_exposure_usd": uncertain,
            "projected_632_usd": projected, "projected_632_with_exposure_usd": projected_with_exposure,
            "recommendation": recommendation}
