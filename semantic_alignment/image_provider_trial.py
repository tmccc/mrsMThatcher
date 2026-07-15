from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import os
import random
import re
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image


SCHEMA_VERSION = 1
SEED = 20260714
PROVIDERS = ("grok", "openai")
XAI_MODEL = "grok-imagine-image-quality"
OPENAI_MODEL = "gpt-image-1.5"
XAI_ENDPOINT = "https://api.x.ai/v1/images/generations"
OPENAI_ENDPOINT = "https://api.openai.com/v1/images/generations"
XAI_COST_PER_IMAGE = 0.10  # Prior project responses: 1e9 USD ticks = $0.10.
OPENAI_COST_PER_IMAGE = 0.013  # Prior project observed prompt + low square output.
MAX_ATTEMPTS = 2
MAX_COST_USD = round(2 * 10 * (XAI_COST_PER_IMAGE + OPENAI_COST_PER_IMAGE), 2)

CATEGORIES = (
    ("domestic_economic_policy", ("tax", "inflation", "econom", "industry", "union", "employment")),
    ("socialism_and_free_enterprise", ("socialis", "free enterprise", "capitalis", "private enterprise")),
    ("liberty_and_responsibility", ("liberty", "freedom", "responsib", "individual")),
    ("patriotism_and_national_identity", ("britain", "british", "nation", "country", "patriot")),
    ("foreign_affairs", ("soviet", "europe", "foreign", "war", "america", "russia", "nato")),
    ("leadership", ("leader", "leadership", "government", "prime minister", "decision")),
    ("humour_or_rhetorical_attack", ("lady", "turn", "attack", "enemy", "consensus", "chicken")),
    ("abstract_political_principle", ("principle", "society", "democracy", "law", "power", "values")),
    ("historically_specific", ("falkland", "miners", "argentina", "berlin", "bruges", "hong kong")),
    ("difficult_visualisation", ("idea", "belief", "choice", "change", "truth", "consensus", "spirit")),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _text(value: Any, limit: int = 700) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value if item)
    value = " ".join(str(value or "").split())
    return value[:limit]


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{4,}", text.lower()) if word not in {"that", "with", "from", "this", "have", "will", "would", "their"}}


def _near_duplicate(a: str, b: str) -> bool:
    aa, bb = _tokens(a), _tokens(b)
    return bool(aa and bb and len(aa & bb) / len(aa | bb) >= 0.68)


def prior_generation_quote_ids(research_root: Path) -> set[str]:
    result: set[str] = set()
    patterns = ("generation_prompt_pilot*", "scene_grammar*", "pairwise_*", "first_impression*")
    for pattern in patterns:
        for path in research_root.glob(f"{pattern}/**/*.json"):
            if path.stat().st_size > 5_000_000:
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            stack = [data]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    for key in ("quote_id", "quote_hash"):
                        candidate = value.get(key)
                        if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{64}", candidate):
                            result.add(candidate)
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
    return result


def _packet_quality(packet: dict[str, Any]) -> bool:
    guidance = packet.get("editorial_guidance") or {}
    return (
        packet.get("validation_status") == "valid"
        and packet.get("research_confidence") in {"high", "medium"}
        and len(_text(packet.get("historical_context"))) >= 40
        and len(_text(packet.get("intended_argument"))) >= 30
        and len(_text(guidance.get("desired_first_impression"))) >= 15
    )


def select_packets(packets: dict[str, dict[str, Any]], excluded: set[str], count: int = 10) -> list[dict[str, Any]]:
    if count != 10:
        raise ValueError("this frozen pilot requires exactly 10 quotations")
    pool = [(qid, p) for qid, p in packets.items() if qid not in excluded and _packet_quality(p)]
    selected: list[dict[str, Any]] = []
    for category, keywords in CATEGORIES:
        ranked = []
        for qid, packet in pool:
            if any(row["quote_id"] == qid for row in selected):
                continue
            quote = packet["quote_text"]
            if any(_near_duplicate(quote, row["quote_text"]) for row in selected):
                continue
            haystack = " ".join(_text(packet.get(key), 1200) for key in ("quote_text", "historical_context", "intended_argument", "broader_principle", "mechanism")).lower()
            hits = sum(1 for word in keywords if word in haystack)
            specificity = bool(packet.get("date")) + bool(packet.get("source_event")) + min(len(packet.get("entities") or []), 3) / 3
            tie = hashlib.sha256(f"{SEED}:{category}:{qid}".encode()).hexdigest()
            ranked.append((hits, specificity, tie, qid, packet))
        if not ranked:
            raise RuntimeError(f"no eligible quotation for {category}")
        ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
        _, _, _, qid, packet = ranked[0]
        selected.append({"quote_id": qid, "quote_text": packet["quote_text"], "category": category, "selection_rationale": f"Strongest deterministic eligible match for {category.replace('_', ' ')}; complete historical and editorial guidance; not used in scanned prior generation studies.", "packet": packet})
    return selected


def canonical_prompt(packet: dict[str, Any]) -> str:
    guidance = packet.get("editorial_guidance") or {}
    entities = ", ".join(_text(x, 100) for x in (packet.get("entities") or [])[:8]) or "none essential"
    return f"""Create one editorial image to accompany this Margaret Thatcher quotation on X.

QUOTATION (do not render this text in the image):
{_text(packet['quote_text'], 1200)}

HISTORICAL AND SEMANTIC BRIEF
- Verification: {_text(packet.get('verification_status'))}. Verified wording, if materially relevant: {_text(packet.get('verified_text'), 800)}
- Event and date: {_text(packet.get('source_event'))}; {_text(packet.get('date'))}.
- Historical setting: {_text(packet.get('historical_context'))}
- Immediate subject: {_text(packet.get('immediate_subject'))}
- Intended argument: {_text(packet.get('intended_argument'))}
- Literal meaning: {_text(packet.get('literal_meaning'))}
- Broader principle: {_text(packet.get('broader_principle'))}
- Mechanism: {_text(packet.get('mechanism'))}
- Claimed consequence: {_text(packet.get('claimed_consequence'))}
- Relevant people, places, organisations, or events: {entities}
- Desired first impression: {_text(guidance.get('desired_first_impression'))}
- Historical requirements: {_text(guidance.get('historical_requirements'))}
- What should dominate: {_text(guidance.get('must_be_visually_dominant'))}
- What must not dominate: {_text(guidance.get('must_not_dominate'))}
- Common misleading visual interpretations to avoid: {_text(guidance.get('common_visual_mistakes'))}
- Research confidence: {_text(packet.get('research_confidence'))}

IMAGE DIRECTION
Communicate the intended meaning immediately at social-media size through one strong, coherent visual concept. Use a realistic, historically credible editorial aesthetic rather than advertising. Margaret Thatcher may appear only when naturally appropriate. Avoid generic Westminster imagery unless the history requires it, unfocused collages, excessive written text, invented quotation text, misleading symbols, and anachronisms. Keep a clear focal hierarchy and strong thumbnail readability. Produce a square 1:1 composition with no border or caption."""


def provider_payload(provider: str, prompt: str) -> dict[str, Any]:
    if provider == "grok":
        return {"model": XAI_MODEL, "prompt": prompt, "n": 1, "response_format": "url", "aspect_ratio": "1:1", "resolution": "1k"}
    if provider == "openai":
        return {"model": OPENAI_MODEL, "prompt": prompt, "n": 1, "quality": "low", "size": "1024x1024", "output_format": "png"}
    raise ValueError(provider)


def prepare_trial(research_run: Path, trial: Path, count: int = 10) -> dict[str, Any]:
    packets_doc = read_json(research_run / "research_packets.json")
    packets = packets_doc.get("items", {})
    excluded = prior_generation_quote_ids(research_run.parent)
    selected = select_packets(packets, excluded, count)
    for folder in ("prompts", "images/grok", "images/openai", "requests/grok", "requests/openai", "responses/grok", "responses/openai", "thumbnails", "review", "reports"):
        (trial / folder).mkdir(parents=True, exist_ok=True)
    items = []
    blind = {}
    for row in selected:
        qid, packet = row["quote_id"], row.pop("packet")
        prompt = canonical_prompt(packet)
        prompt_hash = sha256_bytes(prompt.encode())
        atomic_write_bytes(trial / "prompts" / f"{qid}.txt", prompt.encode())
        prompt_meta = {"quote_id": qid, "prompt_sha256": prompt_hash, "prompt_version": "canonical-historical-image-v1", "source_packet_fields": ["quote_text", "verified_text", "verification_status", "source_event", "date", "historical_context", "immediate_subject", "intended_argument", "literal_meaning", "broader_principle", "mechanism", "claimed_consequence", "entities", "editorial_guidance", "research_confidence"]}
        atomic_write_json(trial / "prompts" / f"{qid}.json", prompt_meta)
        request_hashes = {}
        for provider in PROVIDERS:
            payload = provider_payload(provider, prompt)
            request_hashes[provider] = sha256_bytes(payload["prompt"].encode())
            atomic_write_json(trial / "requests" / provider / f"{qid}.json", {"provider": provider, "endpoint": XAI_ENDPOINT if provider == "grok" else OPENAI_ENDPOINT, "payload": payload, "substantive_prompt_sha256": request_hashes[provider], "prepared_only": True})
        if len(set(request_hashes.values())) != 1 or prompt_hash not in request_hashes.values():
            raise RuntimeError("provider prompt parity failed")
        assignment = ["grok", "openai"]
        random.Random(f"{SEED}:{qid}").shuffle(assignment)
        blind[qid] = {"A": assignment[0], "B": assignment[1]}
        items.append({**row, "historical_context_summary": _text(packet.get("historical_context"), 360), "prompt_sha256": prompt_hash, "provider_prompt_sha256": request_hashes, "prompt_parity": True})
    manifest = {"schema_version": SCHEMA_VERSION, "trial_id": trial.name, "seed": SEED, "case_count": len(items), "planned_provider_calls": 20, "items": items, "created_at": utc_now(), "source_research_run": str(research_run), "source_packet_count": len(packets), "excluded_prior_generation_quote_ids": len(excluded)}
    manifest["manifest_sha256"] = sha256_bytes(json.dumps(items, sort_keys=True).encode())
    atomic_write_json(trial / "manifest.json", manifest)
    atomic_write_json(trial / "review" / "blind_map.json", {"schema_version": 1, "seed": SEED, "assignments": blind})
    atomic_write_json(trial / "review" / "decisions.json", {"schema_version": 1, "items": {}})
    write_selection_report(trial, manifest)
    preflight = build_preflight(trial, manifest)
    atomic_write_json(trial / "reports" / "preflight.json", preflight)
    atomic_write_bytes(trial / "reports" / "preflight.md", render_preflight(preflight).encode())
    return manifest


def write_selection_report(trial: Path, manifest: dict[str, Any]) -> None:
    lines = ["# Image Provider Trial 001 Selection", "", "The ten cases were selected deterministically into predefined editorial categories from valid, medium/high-confidence packets, excluding quote IDs found in prior generation, scene-grammar, pairwise, and first-impression studies. Near-duplicate wording was rejected.", ""]
    for index, row in enumerate(manifest["items"], 1):
        lines += [f"## {index}. {row['category'].replace('_', ' ').title()}", "", f"> {row['quote_text']}", "", row["selection_rationale"], ""]
    atomic_write_bytes(trial / "selection_report.md", ("\n".join(lines) + "\n").encode())


def build_preflight(trial: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    expected = 10 * XAI_COST_PER_IMAGE + 10 * OPENAI_COST_PER_IMAGE
    maximum = expected * MAX_ATTEMPTS
    parity = all(row["prompt_parity"] and len(set(row["provider_prompt_sha256"].values())) == 1 for row in manifest["items"])
    return {"schema_version": 1, "providers": {"grok": {"model": XAI_MODEL, "calls": 10, "settings": {"aspect_ratio": "1:1", "resolution": "1k", "n": 1}, "expected_cost_usd": 1.0, "maximum_cost_usd": 2.0, "pricing_basis": "prior project API-reported cost ticks"}, "openai": {"model": OPENAI_MODEL, "calls": 10, "settings": {"quality": "low", "size": "1024x1024", "n": 1, "output_format": "png"}, "expected_cost_usd": 0.13, "maximum_cost_usd": 0.26, "pricing_basis": "prior project observed request cost"}}, "planned_calls": 20, "expected_images": 20, "expected_cost_usd": round(expected, 2), "conservative_maximum_cost_usd": round(maximum, 2), "required_exact_confirmation_usd": MAX_COST_USD, "maximum_attempts_per_provider_quote": MAX_ATTEMPTS, "prompt_parity_verified": parity, "prompt_hashes": {row["quote_id"]: row["prompt_sha256"] for row in manifest["items"]}, "output_directory": str(trial), "network_calls_made": False, "paid_execution_allowed": len(manifest["items"]) == 10 and parity and maximum <= MAX_COST_USD}


def render_preflight(pf: dict[str, Any]) -> str:
    return "\n".join(["# Image Provider Trial 001 Preflight", "", f"- Models: `{XAI_MODEL}` and `{OPENAI_MODEL}`", "- Planned calls/images: 20 / 20", f"- Expected cost: **${pf['expected_cost_usd']:.2f}**", f"- Conservative maximum with one transport retry per pair: **${pf['conservative_maximum_cost_usd']:.2f}**", f"- Exact required confirmation: **${pf['required_exact_confirmation_usd']:.2f}**", f"- Prompt parity: `{pf['prompt_parity_verified']}`", f"- Output: `{pf['output_directory']}`", "- Preparation made no network or paid calls.", ""])


def _safe_response(raw: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(raw))
    for item in value.get("data", []) if isinstance(value, dict) else []:
        if isinstance(item, dict) and item.get("b64_json"):
            item["b64_json"] = "[decoded image stored separately]"
    return value


def _transient(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.RequestException) and (response is None or response.status_code == 429 or response.status_code >= 500)


def _generate_one(provider: str, prompt: str, api_key: str, session: requests.Session) -> tuple[bytes, dict[str, Any]]:
    payload = provider_payload(provider, prompt)
    endpoint = XAI_ENDPOINT if provider == "grok" else OPENAI_ENDPOINT
    response = session.post(endpoint, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=(30, 600))
    if response.status_code >= 400:
        raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:1200]}", response=response)
    raw = response.json()
    item = raw["data"][0]
    if item.get("b64_json"):
        image = base64.b64decode(item["b64_json"], validate=True)
    elif item.get("url"):
        downloaded = session.get(item["url"], timeout=(30, 600))
        downloaded.raise_for_status()
        image = downloaded.content
    else:
        raise ValueError("provider response has no image data")
    return image, _safe_response(raw)


def generate_trial(trial: Path, confirmed_cost: float, session: requests.Session | None = None, sleep=time.sleep) -> dict[str, Any]:
    if confirmed_cost != MAX_COST_USD:
        raise RuntimeError(f"exact --confirm-max-cost-usd {MAX_COST_USD:.2f} required")
    manifest = read_json(trial / "manifest.json")
    preflight = build_preflight(trial, manifest)
    if not preflight["paid_execution_allowed"]:
        raise RuntimeError("preflight failed")
    keys = {"grok": os.environ.get("XAI_API_KEY", ""), "openai": os.environ.get("OPENAI_API_KEY", "")}
    if not all(keys.values()):
        raise RuntimeError("XAI_API_KEY and OPENAI_API_KEY are required for explicit execution")
    state_path = trial / "generation_state.json"
    state = read_json(state_path, {"schema_version": 1, "items": {}, "attempts": [], "known_cost_usd": {"grok": 0.0, "openai": 0.0}})
    session = session or requests.Session()
    for row in manifest["items"]:
        qid = row["quote_id"]
        prompt = (trial / "prompts" / f"{qid}.txt").read_text()
        for provider in PROVIDERS:
            key = f"{provider}:{qid}"
            if state["items"].get(key, {}).get("status") == "completed":
                continue
            for attempt in range(1, MAX_ATTEMPTS + 1):
                record = {"provider": provider, "quote_id": qid, "attempt": attempt, "status": "sending", "started_at": utc_now(), "prompt_sha256": sha256_bytes(prompt.encode())}
                state["attempts"].append(record); atomic_write_json(state_path, state)
                started = time.monotonic()
                try:
                    image, response = _generate_one(provider, prompt, keys[provider], session)
                    ext = ".png"
                    image_path = trial / "images" / provider / f"{qid}{ext}"
                    atomic_write_bytes(image_path, image)
                    with Image.open(io.BytesIO(image)) as im:
                        dimensions = [im.width, im.height]
                        thumb = im.convert("RGB"); thumb.thumbnail((640, 640))
                        buffer = io.BytesIO(); thumb.save(buffer, "JPEG", quality=88, optimize=True)
                    atomic_write_bytes(trial / "thumbnails" / f"{provider}_{qid}.jpg", buffer.getvalue())
                    response_meta = {"provider": provider, "quote_id": qid, "attempt": attempt, "response": response, "received_at": utc_now()}
                    atomic_write_json(trial / "responses" / provider / f"{qid}.json", response_meta)
                    cost = XAI_COST_PER_IMAGE if provider == "grok" else OPENAI_COST_PER_IMAGE
                    if provider == "grok":
                        ticks = (response.get("usage") or {}).get("cost_in_usd_ticks")
                        if isinstance(ticks, (int, float)) and ticks >= 0:
                            cost = ticks / 10_000_000_000
                    state["known_cost_usd"][provider] = round(state["known_cost_usd"][provider] + cost, 6)
                    record.update({"status": "completed", "elapsed_seconds": round(time.monotonic() - started, 3), "completed_at": utc_now()})
                    state["items"][key] = {"status": "completed", "provider": provider, "quote_id": qid, "model": XAI_MODEL if provider == "grok" else OPENAI_MODEL, "image_path": str(image_path), "sha256": sha256_bytes(image), "dimensions": dimensions, "generated_at": utc_now(), "request_id": response.get("id"), "usage": response.get("usage", {}), "attempts": attempt, "estimated_cost_usd": cost}
                    atomic_write_json(state_path, state)
                    break
                except Exception as exc:
                    record.update({"status": "confirmed_failure", "elapsed_seconds": round(time.monotonic() - started, 3), "error_type": type(exc).__name__, "error": str(exc)[:1600], "transient": _transient(exc), "finished_at": utc_now()})
                    atomic_write_json(state_path, state)
                    if not record["transient"] or attempt == MAX_ATTEMPTS:
                        state["items"][key] = {"status": "failed", "attempts": attempt, "error": record["error"]}; atomic_write_json(state_path, state)
                        break
                    sleep(2 ** attempt)
    return state


def trial_status(trial: Path) -> dict[str, Any]:
    manifest = read_json(trial / "manifest.json", {"items": []})
    state = read_json(trial / "generation_state.json", {"items": {}, "attempts": [], "known_cost_usd": {"grok": 0, "openai": 0}})
    decisions = read_json(trial / "review" / "decisions.json", {"items": {}})["items"]
    complete = {p: sum(state["items"].get(f"{p}:{row['quote_id']}", {}).get("status") == "completed" for row in manifest["items"]) for p in PROVIDERS}
    return {"trial_dir": str(trial), "cases": len(manifest["items"]), "generated": complete, "planned_images": 20, "attempts": len(state["attempts"]), "known_cost_usd": state["known_cost_usd"], "reviews_completed": len(decisions), "review_complete": len(decisions) == len(manifest["items"]) and bool(manifest["items"])}


REASON_TAGS = ("stronger immediate impact", "better historical fit", "better representation of meaning", "better composition", "better likeness", "less generic", "fewer artefacts", "less misleading", "other")


def serve_review(trial: Path, host: str, port: int) -> None:
    manifest = read_json(trial / "manifest.json")
    blind = read_json(trial / "review" / "blind_map.json")["assignments"]
    decisions_path = trial / "review" / "decisions.json"
    rows = manifest["items"]
    by_id = {row["quote_id"]: row for row in rows}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return self._redirect(f"/case/{rows[0]['quote_id']}")
            if parsed.path.startswith("/case/"):
                qid = parsed.path.rsplit("/", 1)[-1]
                if qid not in by_id: return self._send(404, b"not found", "text/plain")
                return self._page(qid)
            if parsed.path.startswith("/asset/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 3 or parts[1] not in by_id or parts[2] not in {"A", "B"}: return self._send(404, b"not found", "text/plain")
                provider = blind[parts[1]][parts[2]]
                path = trial / "images" / provider / f"{parts[1]}.png"
                if not path.exists(): return self._send(404, b"image unavailable", "text/plain")
                return self._send(200, path.read_bytes(), "image/png")
            if parsed.path == "/results":
                decisions = read_json(decisions_path, {"items": {}})["items"]
                if len(decisions) < len(rows): return self._send(409, b"Complete all ten reviews before opening results.", "text/plain")
                body = report_results(trial).encode(); return self._send(200, b"<pre>" + html.escape(body.decode()).encode() + b"</pre>", "text/html; charset=utf-8")
            return self._send(404, b"not found", "text/plain")

        def _redirect(self, location: str) -> None:
            self.send_response(303); self.send_header("Location", location); self.end_headers()

        def _page(self, qid: str) -> None:
            row = by_id[qid]; index = next(i for i, item in enumerate(rows) if item["quote_id"] == qid)
            saved = read_json(decisions_path, {"items": {}})["items"]; prior = saved.get(qid, {})
            choices = "".join(f'<label><input type="radio" name="choice" value="{value}" {"checked" if prior.get("choice")==value else ""} required>{label}</label>' for value, label in (("A", "A is better"), ("B", "B is better"), ("equal", "Roughly equal"), ("neither", "Neither is acceptable")))
            tags = "".join(f'<label><input type="checkbox" name="reason" value="{html.escape(tag)}" {"checked" if tag in prior.get("reasons",[]) else ""}>{html.escape(tag)}</label>' for tag in REASON_TAGS)
            previous = rows[max(0, index-1)]["quote_id"]; next_id = rows[min(len(rows)-1, index+1)]["quote_id"]
            page = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Blind image review</title><style>body{{font:16px system-ui;max-width:1500px;margin:auto;padding:16px;background:#f2f2f0;color:#171717}}nav{{display:flex;justify-content:space-between;gap:12px}}blockquote{{font-size:1.25rem;background:white;border-left:4px solid #a51d2d;padding:14px}}.context{{max-width:900px}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}figure{{margin:0;background:#111;padding:8px;color:white;text-align:center}}img{{width:100%;aspect-ratio:1;object-fit:contain}}fieldset{{margin:16px 0;background:white;padding:12px}}label{{display:inline-block;margin:8px 14px 8px 0}}textarea{{width:100%;min-height:70px}}button,a{{padding:11px 14px}}@media(max-width:800px){{.pair{{grid-template-columns:1fr}}}}</style></head><body><nav><a href="/case/{previous}">Previous</a><strong>{index+1}/10 · reviewed {len(saved)}/10</strong><a href="/case/{next_id}">Next</a></nav><blockquote>{html.escape(row['quote_text'])}</blockquote><p class="context">{html.escape(row['historical_context_summary'])}</p><form method="post"><div class="pair"><figure><a href="/asset/{qid}/A" target="_blank"><img src="/asset/{qid}/A" alt="Candidate A"></a><figcaption>Image A</figcaption></figure><figure><a href="/asset/{qid}/B" target="_blank"><img src="/asset/{qid}/B" alt="Candidate B"></a><figcaption>Image B</figcaption></figure></div><fieldset><legend>Which is better?</legend>{choices}</fieldset><fieldset><legend>Optional reasons</legend>{tags}</fieldset><label>Optional note<textarea name="note">{html.escape(prior.get('note',''))}</textarea></label><p><button type="submit">Save and next</button></p></form></body></html>'''
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if not self.path.startswith("/case/"): return self._send(404, b"not found", "text/plain")
            qid = self.path.rsplit("/", 1)[-1]
            if qid not in by_id: return self._send(404, b"not found", "text/plain")
            length = int(self.headers.get("Content-Length", "0")); form = parse_qs(self.rfile.read(length).decode())
            choice = (form.get("choice") or [""])[0]
            if choice not in {"A", "B", "equal", "neither"}: return self._send(400, b"invalid choice", "text/plain")
            data = read_json(decisions_path, {"schema_version": 1, "items": {}})
            data["items"][qid] = {"quote_id": qid, "choice": choice, "reasons": [x for x in form.get("reason", []) if x in REASON_TAGS], "note": (form.get("note") or [""])[0][:2000], "saved_at": utc_now()}
            atomic_write_json(decisions_path, data)
            index = next(i for i, item in enumerate(rows) if item["quote_id"] == qid)
            self._redirect(f"/case/{rows[min(index+1,len(rows)-1)]['quote_id']}")

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def report_results(trial: Path) -> str:
    manifest = read_json(trial / "manifest.json"); blind = read_json(trial / "review" / "blind_map.json")["assignments"]
    decisions = read_json(trial / "review" / "decisions.json", {"items": {}})["items"]
    if len(decisions) != len(manifest["items"]):
        raise RuntimeError("all ten reviews must be completed")
    state = read_json(trial / "generation_state.json", {"known_cost_usd": {"grok": 0, "openai": 0}})
    counts = {"grok": 0, "openai": 0, "equal": 0, "neither": 0}; rows = []
    reason_counts = {tag: 0 for tag in REASON_TAGS}
    for item in manifest["items"]:
        qid = item["quote_id"]; decision = decisions[qid]; choice = decision["choice"]
        winner = blind[qid].get(choice, choice)
        counts[winner] += 1
        for reason in decision["reasons"]:
            reason_counts[reason] += 1
        rows.append({"quote_id": qid, "category": item["category"], "choice": choice, "winner": winner, "provider_a": blind[qid]["A"], "provider_b": blind[qid]["B"], "reasons": "; ".join(decision["reasons"]), "note": decision["note"]})
    publishable_lower_bound = {"grok": counts["grok"] + counts["equal"], "openai": counts["openai"] + counts["equal"]}
    atomic_write_json(trial / "review" / "review_results.json", {"schema_version": 1, "counts": counts, "publishable_lower_bound": publishable_lower_bound, "reason_tag_counts": reason_counts, "items": rows})
    with io.StringIO() as buffer:
        writer = csv.DictWriter(buffer, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
        atomic_write_bytes(trial / "review" / "review_results.csv", buffer.getvalue().encode())
    decisive = counts["grok"] + counts["openai"]
    costs = state["known_cost_usd"]
    lines = ["# Image Provider Trial 001 Review", "", "This is a ten-case pilot and does not establish general provider superiority.", "", f"- Grok wins: {counts['grok']}", f"- OpenAI wins: {counts['openai']}", f"- Roughly equal: {counts['equal']}", f"- Neither acceptable: {counts['neither']}", f"- Grok win rate excluding ties/neither: {counts['grok']/decisive:.1%}" if decisive else "- Decisive win rate: unavailable", f"- OpenAI win rate excluding ties/neither: {counts['openai']/decisive:.1%}" if decisive else "", f"- Grok conservative publishable indication: {publishable_lower_bound['grok']}/10", f"- OpenAI conservative publishable indication: {publishable_lower_bound['openai']}/10", "", "The publishable indication is deliberately conservative: a provider counts only when its image won or the pair was rated equal. A losing image was not separately judged unpublishable.", "", f"- Estimated cost per Grok preferred image: ${costs.get('grok',0)/counts['grok']:.3f}" if counts['grok'] else "- Grok cost per preferred image: unavailable", f"- Estimated cost per OpenAI preferred image: ${costs.get('openai',0)/counts['openai']:.3f}" if counts['openai'] else "- OpenAI cost per preferred image: unavailable", "", "## Results By Theme", "", "| Theme | Outcome | Reason tags |", "|---|---|---|"]
    lines += [f"| {row['category'].replace('_', ' ')} | {row['winner']} | {row['reasons'] or 'none recorded'} |" for row in rows]
    lines += ["", "## Reason Tags", ""] + [f"- {tag}: {count}" for tag, count in reason_counts.items() if count]
    lines += ["", "## Integrity", "", "Prompt hashes and blind assignments remain in the trial audit files. Both provider request records contain the same substantive prompt hash per quotation.", ""]
    report = "\n".join(lines)
    atomic_write_bytes(trial / "review" / "review_report.md", report.encode())
    return report
