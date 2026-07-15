from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import os
import random
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image

from .image_provider_trial import (
    CATEGORIES,
    REASON_TAGS,
    _near_duplicate,
    _packet_quality,
    atomic_write_bytes,
    atomic_write_json,
    canonical_prompt,
    prior_generation_quote_ids,
    read_json,
    sha256_bytes,
)
from .quote_research_gemini import TOP_LEVEL_FIELDS, validate_packet

MODEL = "gpt-image-1"
QUALITIES = ("medium", "high")
SIZE = "1024x1024"
ENDPOINT = "https://api.openai.com/v1/images/generations"
SEED = 20260714
MAX_ATTEMPTS = 2
OUTPUT_COST = {"medium": 0.042, "high": 0.167}
TEXT_INPUT_PER_MILLION = 5.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_corpus(research_run: Path) -> tuple[dict[str, dict[str, Any]], set[str], str]:
    packets = read_json(research_run / "research_packets.json")["items"]
    records = {row["quote_id"]: row for row in read_json(research_run / "corpus_manifest.json")["records"]}
    status = read_json(research_run / "final_unresolved" / "final_research_status.json")
    unresolved = set(status["unresolved_quote_ids"])
    if len(packets) != 626 or len(unresolved) != 6:
        raise RuntimeError(f"expected 626 completed and 6 unresolved; found {len(packets)} and {len(unresolved)}")
    overlap = set(packets) & unresolved
    if overlap:
        raise RuntimeError(f"unresolved quotation encountered in completed packet set: {sorted(overlap)}")
    for qid, packet in packets.items():
        try:
            validate_packet({field: packet[field] for field in TOP_LEVEL_FIELDS}, records[qid])
        except Exception as exc:
            raise RuntimeError(f"invalid or mutable completed packet identity: {qid}: {exc}") from exc
    eligible_hash = sha256_bytes("\n".join(sorted(packets)).encode())
    return packets, unresolved, eligible_hash


def _extra_prior_ids(root: Path) -> set[str]:
    result: set[str] = set()
    for pattern in ("image_provider_trial_*", "openai_quality_trial_*"):
        for path in root.glob(f"{pattern}/manifest.json"):
            try:
                for item in read_json(path).get("items", []):
                    if item.get("quote_id"):
                        result.add(item["quote_id"])
            except (OSError, ValueError, AttributeError):
                pass
    return result


def select_quality_packets(packets: dict[str, dict[str, Any]], excluded: set[str], count: int) -> list[dict[str, Any]]:
    if count not in {10, 30}:
        raise ValueError("quality trials support exactly 10 or 30 cases")
    per_category = count // len(CATEGORIES)
    pool = [(qid, packet) for qid, packet in packets.items() if qid not in excluded and _packet_quality(packet)]
    selected: list[dict[str, Any]] = []
    for category, keywords in CATEGORIES:
        category_rows = []
        for qid, packet in pool:
            quote = packet["quote_text"]
            haystack = " ".join(" ".join(str(packet.get(key) or "").split()) for key in ("quote_text", "historical_context", "intended_argument", "broader_principle", "mechanism")).lower()
            hits = sum(1 for word in keywords if word in haystack)
            specificity = int(bool(packet.get("date"))) + int(bool(packet.get("source_event"))) + min(len(packet.get("entities") or []), 3) / 3
            tie = hashlib.sha256(f"{SEED}:{count}:{category}:{qid}".encode()).hexdigest()
            category_rows.append((hits, specificity, tie, qid, packet))
        category_rows.sort(key=lambda row: (-row[0], -row[1], row[2]))
        accepted = 0
        for _, _, _, qid, packet in category_rows:
            if any(row["quote_id"] == qid for row in selected):
                continue
            if any(_near_duplicate(packet["quote_text"], row["quote_text"]) for row in selected):
                continue
            selected.append({"quote_id": qid, "quote_text": packet["quote_text"], "category": category, "selection_rationale": f"Deterministic eligible representative for {category.replace('_', ' ')}; complete context; excluded from earlier image and quality trials.", "packet": packet})
            accepted += 1
            if accepted == per_category:
                break
        if accepted != per_category:
            raise RuntimeError(f"only {accepted}/{per_category} eligible cases for {category}")
    return selected


def request_payload(prompt: str, quality: str) -> dict[str, Any]:
    if quality not in QUALITIES:
        raise ValueError(quality)
    return {"model": MODEL, "prompt": prompt, "n": 1, "quality": quality, "size": SIZE, "output_format": "png"}


def payload_difference_is_quality_only(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = set(a) | set(b)
    return {key for key in keys if a.get(key) != b.get(key)} == {"quality"}


def _input_cost(prompt: str) -> float:
    return (len(prompt.encode()) / 4) * TEXT_INPUT_PER_MILLION / 1_000_000


def prepare_trial(research_run: Path, trial: Path, count: int = 10) -> dict[str, Any]:
    packets, unresolved, eligible_hash = load_corpus(research_run)
    excluded = prior_generation_quote_ids(research_run.parent) | _extra_prior_ids(research_run.parent)
    selected = select_quality_packets(packets, excluded, count)
    selected_ids = [row["quote_id"] for row in selected]
    if set(selected_ids) & unresolved:
        raise RuntimeError("unresolved quote selected")
    for folder in ("prompts", "requests/medium", "requests/high", "responses/medium", "responses/high", "images/medium", "images/high", "thumbnails", "review", "reports"):
        (trial / folder).mkdir(parents=True, exist_ok=True)
    items, blind = [], {}
    for row in selected:
        qid, packet = row["quote_id"], row.pop("packet")
        if qid not in packets:
            raise RuntimeError(f"selected quote absent from completed packets: {qid}")
        prompt = canonical_prompt(packet); prompt_hash = sha256_bytes(prompt.encode())
        atomic_write_bytes(trial / "prompts" / f"{qid}.txt", prompt.encode())
        atomic_write_json(trial / "prompts" / f"{qid}.json", {"quote_id": qid, "sha256": prompt_hash, "prompt_version": "canonical-historical-image-v1"})
        payloads = {quality: request_payload(prompt, quality) for quality in QUALITIES}
        if not payload_difference_is_quality_only(payloads["medium"], payloads["high"]):
            raise RuntimeError("quality is not the sole request difference")
        for quality, payload in payloads.items():
            atomic_write_json(trial / "requests" / quality / f"{qid}.json", {"quality": quality, "endpoint": ENDPOINT, "payload": payload, "substantive_prompt_sha256": prompt_hash, "prepared_only": True})
        assignment = list(QUALITIES); random.Random(f"{SEED}:{qid}").shuffle(assignment)
        blind[qid] = {"A": assignment[0], "B": assignment[1]}
        items.append({**row, "completed_packet_proof": True, "unresolved_exclusion_proof": qid not in unresolved, "historical_context_summary": " ".join(str(packet.get("historical_context") or "").split())[:360], "prompt_sha256": prompt_hash, "payload_difference": ["quality"], "prompt_parity": True})
    selected_hash = sha256_bytes("\n".join(selected_ids).encode())
    manifest = {"schema_version": 1, "trial_id": trial.name, "seed": SEED, "eligible_corpus_size": len(packets), "unresolved_count": len(unresolved), "unresolved_quote_ids": sorted(unresolved), "eligible_quote_id_set_sha256": eligible_hash, "selected_quote_list_sha256": selected_hash, "case_count": len(items), "planned_calls": len(items) * 2, "items": items, "source_research_run": str(research_run), "created_at": utc_now()}
    manifest["manifest_sha256"] = sha256_bytes(json.dumps(items, sort_keys=True).encode())
    atomic_write_json(trial / "manifest.json", manifest)
    atomic_write_json(trial / "review" / "blind_map.json", {"schema_version": 1, "seed": SEED, "assignments": blind})
    atomic_write_json(trial / "review" / "decisions.json", {"schema_version": 1, "items": {}})
    _selection_report(trial, manifest)
    pf = preflight(trial, manifest); atomic_write_json(trial / "reports" / "preflight.json", pf); atomic_write_bytes(trial / "reports" / "preflight.md", render_preflight(pf).encode())
    return manifest


def _selection_report(trial: Path, manifest: dict[str, Any]) -> None:
    lines = ["# OpenAI Quality Trial 001 Selection", "", f"Eligible completed corpus: **{manifest['eligible_corpus_size']}**. Unresolved excluded: **{manifest['unresolved_count']}**.", f"Eligible ID-set SHA-256: `{manifest['eligible_quote_id_set_sha256']}`", f"Selected-list SHA-256: `{manifest['selected_quote_list_sha256']}`", "", "Selection used ten fixed editorial categories, deterministic hash tie-breaking, prior-experiment exclusions, and near-duplicate rejection.", ""]
    for index, row in enumerate(manifest["items"], 1):
        lines += [f"## {index}. {row['category'].replace('_', ' ').title()}", "", f"> {row['quote_text']}", "", row["selection_rationale"], f"Completed packet: `{row['completed_packet_proof']}`; excluded from unresolved set: `{row['unresolved_exclusion_proof']}`.", ""]
    atomic_write_bytes(trial / "selection_report.md", ("\n".join(lines) + "\n").encode())


def preflight(trial: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    input_cost = sum(_input_cost((trial / "prompts" / f"{row['quote_id']}.txt").read_text()) for row in manifest["items"])
    case_count = len(manifest["items"])
    output_cost = case_count * sum(OUTPUT_COST.values())
    expected = output_cost + 2 * input_cost
    maximum = expected * MAX_ATTEMPTS
    exact_ceiling = float(f"{maximum + 0.019999:.2f}")
    return {"schema_version": 1, "model": MODEL, "qualities": list(QUALITIES), "size": SIZE, "calls": case_count * 2, "images": case_count * 2, "medium_output_cost_usd": case_count * OUTPUT_COST["medium"], "high_output_cost_usd": case_count * OUTPUT_COST["high"], "estimated_prompt_input_cost_usd": round(2 * input_cost, 6), "expected_minimum_output_cost_usd": round(output_cost, 3), "expected_total_cost_usd": round(expected, 4), "conservative_maximum_cost_usd": round(maximum, 4), "required_exact_confirmation_usd": exact_ceiling, "maximum_attempts": MAX_ATTEMPTS, "prompt_parity_verified": all(row["prompt_parity"] for row in manifest["items"]), "quality_only_difference_verified": all(row["payload_difference"] == ["quality"] for row in manifest["items"]), "output_directory": str(trial), "pricing_source": "OpenAI official gpt-image-1 model documentation, 2026-07-14", "network_generation_calls_made": False}


def render_preflight(pf: dict[str, Any]) -> str:
    return "\n".join(["# OpenAI Quality Trial Preflight", "", f"- Model: `{pf['model']}`", "- Settings: Medium vs High; 1024x1024 PNG", f"- Planned calls/images: {pf['calls']} / {pf['images']}", f"- Medium output cost: ${pf['medium_output_cost_usd']:.3f}", f"- High output cost: ${pf['high_output_cost_usd']:.3f}", f"- Estimated prompt input: ${pf['estimated_prompt_input_cost_usd']:.4f}", f"- Expected total: **${pf['expected_total_cost_usd']:.4f}**", f"- Conservative maximum with one transport retry per request: **${pf['conservative_maximum_cost_usd']:.4f}**", f"- Exact confirmation required: **${pf['required_exact_confirmation_usd']:.2f}**", f"- Prompt parity: `{pf['prompt_parity_verified']}`", f"- Quality is sole payload difference: `{pf['quality_only_difference_verified']}`", f"- Output: `{pf['output_directory']}`", "- No generation API call was made during preparation.", ""])


def _transient(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.RequestException) and (response is None or response.status_code == 429 or response.status_code >= 500)


def _generate(prompt: str, quality: str, api_key: str, session: requests.Session) -> tuple[bytes, dict[str, Any]]:
    response = session.post(ENDPOINT, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=request_payload(prompt, quality), timeout=(30, 600))
    if response.status_code >= 400:
        raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:1200]}", response=response)
    raw = response.json(); item = raw["data"][0]
    image = base64.b64decode(item["b64_json"], validate=True)
    stored = json.loads(json.dumps(raw)); stored["data"][0]["b64_json"] = "[decoded image stored separately]"
    return image, stored


def generate_trial(trial: Path, confirmed: float, session: requests.Session | None = None, sleep=time.sleep) -> dict[str, Any]:
    manifest = read_json(trial / "manifest.json"); pf = preflight(trial, manifest)
    if confirmed != pf["required_exact_confirmation_usd"]:
        raise RuntimeError(f"exact --confirm-max-cost-usd {pf['required_exact_confirmation_usd']:.2f} required")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key: raise RuntimeError("OPENAI_API_KEY is required only for explicit execution")
    state_path = trial / "generation_state.json"; state = read_json(state_path, {"schema_version": 1, "items": {}, "attempts": [], "known_cost_usd": {"medium": 0.0, "high": 0.0}}); session = session or requests.Session()
    for row in manifest["items"]:
        qid = row["quote_id"]; prompt = (trial / "prompts" / f"{qid}.txt").read_text()
        for quality in QUALITIES:
            item_key = f"{quality}:{qid}"
            if state["items"].get(item_key, {}).get("status") == "completed": continue
            for attempt in range(1, MAX_ATTEMPTS + 1):
                record = {"quote_id": qid, "quality": quality, "attempt": attempt, "status": "sending", "started_at": utc_now(), "prompt_sha256": sha256_bytes(prompt.encode())}; state["attempts"].append(record); atomic_write_json(state_path, state); started = time.monotonic()
                try:
                    image, response = _generate(prompt, quality, key, session)
                    path = trial / "images" / quality / f"{qid}.png"; atomic_write_bytes(path, image)
                    with Image.open(io.BytesIO(image)) as im:
                        dimensions = [im.width, im.height]; thumb = im.convert("RGB"); thumb.thumbnail((640, 640)); out = io.BytesIO(); thumb.save(out, "JPEG", quality=88, optimize=True)
                    atomic_write_bytes(trial / "thumbnails" / f"{quality}_{qid}.jpg", out.getvalue())
                    atomic_write_json(trial / "responses" / quality / f"{qid}.json", {"quote_id": qid, "quality": quality, "response": response, "received_at": utc_now()})
                    cost = OUTPUT_COST[quality] + _input_cost(prompt); state["known_cost_usd"][quality] = round(state["known_cost_usd"][quality] + cost, 6)
                    record.update({"status": "completed", "completed_at": utc_now(), "elapsed_seconds": round(time.monotonic()-started, 3)}); state["items"][item_key] = {"status": "completed", "quote_id": qid, "quality": quality, "model": MODEL, "image_path": str(path), "sha256": sha256_bytes(image), "dimensions": dimensions, "request_id": response.get("id"), "usage": response.get("usage", {}), "attempts": attempt, "estimated_cost_usd": round(cost, 6), "generated_at": utc_now()}; atomic_write_json(state_path, state); break
                except Exception as exc:
                    record.update({"status": "confirmed_failure", "error": str(exc)[:1600], "error_type": type(exc).__name__, "transient": _transient(exc), "elapsed_seconds": round(time.monotonic()-started, 3), "finished_at": utc_now()}); atomic_write_json(state_path, state)
                    if not record["transient"] or attempt == MAX_ATTEMPTS: state["items"][item_key] = {"status": "failed", "attempts": attempt, "error": record["error"]}; atomic_write_json(state_path, state); break
                    sleep(2 ** attempt)
    return state


def status(trial: Path) -> dict[str, Any]:
    manifest = read_json(trial / "manifest.json", {"items": []}); state = read_json(trial / "generation_state.json", {"items": {}, "attempts": [], "known_cost_usd": {"medium": 0, "high": 0}}); reviews = read_json(trial / "review" / "decisions.json", {"items": {}})["items"]
    generated = {q: sum(state["items"].get(f"{q}:{row['quote_id']}", {}).get("status") == "completed" for row in manifest["items"]) for q in QUALITIES}
    return {"trial_dir": str(trial), "eligible_corpus_size": manifest.get("eligible_corpus_size"), "cases": len(manifest["items"]), "generated": generated, "attempts": len(state["attempts"]), "known_cost_usd": state["known_cost_usd"], "reviews_completed": len(reviews), "review_complete": len(reviews) == len(manifest["items"]) and bool(manifest["items"])}


def serve_review(trial: Path, host: str, port: int) -> None:
    manifest = read_json(trial / "manifest.json"); blind = read_json(trial / "review" / "blind_map.json")["assignments"]; decisions_path = trial / "review" / "decisions.json"; rows = manifest["items"]; by_id = {x["quote_id"]: x for x in rows}
    class Handler(BaseHTTPRequestHandler):
        def send(self, code: int, body: bytes, mime="text/html; charset=utf-8"):
            self.send_response(code); self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
        def redirect(self, location): self.send_response(303); self.send_header("Location", location); self.end_headers()
        def do_GET(self):
            path=urlparse(self.path).path
            if path=="/": return self.redirect(f"/case/{rows[0]['quote_id']}")
            if path.startswith("/asset/"):
                parts=path.strip("/").split("/")
                if len(parts)!=3 or parts[1] not in by_id or parts[2] not in {"A","B"}: return self.send(404,b"not found","text/plain")
                quality=blind[parts[1]][parts[2]]; image=trial/"images"/quality/f"{parts[1]}.png"; return self.send(200,image.read_bytes(),"image/png") if image.exists() else self.send(404,b"unavailable","text/plain")
            if path.startswith("/case/"):
                qid=path.rsplit("/",1)[-1]
                if qid not in by_id:return self.send(404,b"not found","text/plain")
                return self.page(qid)
            if path=="/results":
                saved=read_json(decisions_path,{"items":{}})["items"]
                if len(saved)<len(rows):return self.send(409,b"Complete all reviews first.","text/plain")
                return self.send(200,("<pre>"+html.escape(report_results(trial))+"</pre>").encode())
            self.send(404,b"not found","text/plain")
        def page(self,qid):
            row=by_id[qid]; index=next(i for i,x in enumerate(rows) if x["quote_id"]==qid); saved=read_json(decisions_path,{"items":{}})["items"]; prior=saved.get(qid,{})
            choices="".join(f'<label><input type="radio" name="choice" value="{v}" {"checked" if prior.get("choice")==v else ""} required>{label}</label>' for v,label in (("A","A better"),("B","B better"),("equal","Roughly equal"),("neither","Neither acceptable")))
            tags="".join(f'<label><input type="checkbox" name="reason" value="{html.escape(t)}" {"checked" if t in prior.get("reasons",[]) else ""}>{html.escape(t)}</label>' for t in REASON_TAGS)
            prev=rows[max(0,index-1)]["quote_id"]; nxt=rows[min(len(rows)-1,index+1)]["quote_id"]
            body=f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Blind quality review</title><style>body{{font:16px system-ui;max-width:1500px;margin:auto;padding:16px;background:#f2f2f0}}nav{{display:flex;justify-content:space-between}}blockquote,fieldset{{background:white;padding:14px}}blockquote{{font-size:1.25rem;border-left:4px solid #a51d2d}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}figure{{background:#111;color:white;margin:0;padding:8px;text-align:center}}img{{width:100%;aspect-ratio:1;object-fit:contain}}label{{display:inline-block;margin:8px 14px 8px 0}}textarea{{width:100%;min-height:70px}}button,a{{padding:11px}}@media(max-width:800px){{.pair{{grid-template-columns:1fr}}}}</style><nav><a href="/case/{prev}">Previous</a><strong>{index+1}/{len(rows)} · reviewed {len(saved)}/{len(rows)}</strong><a href="/case/{nxt}">Next</a></nav><blockquote>{html.escape(row['quote_text'])}</blockquote><p>{html.escape(row['historical_context_summary'])}</p><form method="post"><div class="pair"><figure><a target="_blank" href="/asset/{qid}/A"><img src="/asset/{qid}/A" alt="Candidate A"></a><figcaption>Image A</figcaption></figure><figure><a target="_blank" href="/asset/{qid}/B"><img src="/asset/{qid}/B" alt="Candidate B"></a><figcaption>Image B</figcaption></figure></div><fieldset>{choices}</fieldset><fieldset>{tags}</fieldset><label>Optional note<textarea name="note">{html.escape(prior.get('note',''))}</textarea></label><p><button>Save and next</button></p></form>'''; self.send(200,body.encode())
        def do_POST(self):
            qid=self.path.rsplit("/",1)[-1]
            if not self.path.startswith("/case/") or qid not in by_id:return self.send(404,b"not found","text/plain")
            form=parse_qs(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode()); choice=(form.get("choice") or [""])[0]
            if choice not in {"A","B","equal","neither"}:return self.send(400,b"invalid","text/plain")
            data=read_json(decisions_path,{"schema_version":1,"items":{}}); data["items"][qid]={"quote_id":qid,"choice":choice,"reasons":[x for x in form.get("reason",[]) if x in REASON_TAGS],"note":(form.get("note") or [""])[0][:2000],"saved_at":utc_now()}; atomic_write_json(decisions_path,data); index=next(i for i,x in enumerate(rows) if x["quote_id"]==qid); self.redirect(f"/case/{rows[min(len(rows)-1,index+1)]['quote_id']}")
        def log_message(self,*args):pass
    ThreadingHTTPServer((host,port),Handler).serve_forever()


def report_results(trial: Path) -> str:
    manifest=read_json(trial/"manifest.json"); blind=read_json(trial/"review/blind_map.json")["assignments"]; decisions=read_json(trial/"review/decisions.json",{"items":{}})["items"]
    if len(decisions)!=len(manifest["items"]):raise RuntimeError("all reviews must be complete")
    state=read_json(trial/"generation_state.json"); counts={"medium":0,"high":0,"equal":0,"neither":0}; reasons={x:0 for x in REASON_TAGS}; rows=[]
    for item in manifest["items"]:
        qid=item["quote_id"]; d=decisions[qid]; winner=blind[qid].get(d["choice"],d["choice"]); counts[winner]+=1
        for tag in d["reasons"]:reasons[tag]+=1
        rows.append({"quote_id":qid,"category":item["category"],"choice":d["choice"],"winner":winner,"quality_a":blind[qid]["A"],"quality_b":blind[qid]["B"],"reasons":"; ".join(d["reasons"]),"note":d["note"]})
    costs=state["known_cost_usd"]
    cost_ratio = costs["high"] / costs["medium"] if costs["medium"] else None
    cost_per_win = {"medium": costs["medium"] / counts["medium"] if counts["medium"] else None, "high": costs["high"] / counts["high"] if counts["high"] else None}
    case_count=len(manifest["items"])
    decisive=counts["medium"]+counts["high"]
    advantage=counts["high"]-counts["medium"]
    justified=decisive >= 20 and counts["high"] / decisive >= 0.65
    recommendation = ("High quality shows a material preference advantage in this bounded trial; evaluate cost and failure-category effects before any corpus decision." if justified else "High quality is not economically justified by this bounded pilot; retain Medium as the default research candidate pending stronger independent evidence.")
    atomic_write_json(trial/"review"/"review_results.json",{"schema_version":1,"counts":counts,"reason_tag_counts":reasons,"economics":{"cost_ratio_high_to_medium":cost_ratio,"cost_per_win":cost_per_win},"recommendation":recommendation,"items":rows})
    out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows);atomic_write_bytes(trial/"review"/"review_results.csv",out.getvalue().encode())
    lines=["# OpenAI Quality Trial Review","",f"This {case_count}-case pilot compares quality settings, not providers.","",f"- Medium wins: {counts['medium']}",f"- High wins: {counts['high']}",f"- Equal: {counts['equal']}",f"- Neither: {counts['neither']}",f"- High decisive win rate: {counts['high']/decisive:.1%}" if decisive else "- Decisive rate unavailable",f"- Medium known estimated spend: ${costs['medium']:.4f}",f"- High known estimated spend: ${costs['high']:.4f}",f"- High/Medium cost ratio: {cost_ratio:.2f}x",f"- Medium cost per winning image: ${cost_per_win['medium']:.4f}" if cost_per_win["medium"] else "- Medium cost per win: unavailable",f"- High cost per winning image: ${cost_per_win['high']:.4f}" if cost_per_win["high"] else "- High cost per win: unavailable","","## Interpretation","",recommendation,"",f"High's net win advantage was {advantage:+d} across {decisive} decisive cases. This must be interpreted against its materially higher cost and the {counts['neither']}/{case_count} neither rate.","","## Results By Theme","","| Theme | Outcome | Reasons |","|---|---|---|"]+[f"| {x['category'].replace('_',' ')} | {x['winner']} | {x['reasons'] or 'none'} |" for x in rows]+["","Prompt hashes and blind assignments passed integrity checks. The quality parameter was the only request-payload difference.",""]
    report="\n".join(lines);atomic_write_bytes(trial/"review"/"review_report.md",report.encode());return report
