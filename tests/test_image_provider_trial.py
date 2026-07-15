import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from semantic_alignment.image_provider_trial import (
    MAX_COST_USD,
    canonical_prompt,
    generate_trial,
    prepare_trial,
    provider_payload,
    report_results,
    select_packets,
)


def packet(qid, text, keyword):
    return {
        "quote_id": qid, "quote_text": f"{text} {keyword}", "verified_text": f"{text} {keyword}",
        "verification_status": "exact", "validation_status": "valid", "research_confidence": "high",
        "date": "1985-01-01", "source_event": "Speech", "historical_context": "A sufficiently detailed historical context for this controlled test case.",
        "immediate_subject": keyword, "intended_argument": "A sufficiently detailed intended political argument for testing.",
        "literal_meaning": keyword, "broader_principle": keyword, "mechanism": keyword,
        "claimed_consequence": "A concrete consequence follows from the stated mechanism.", "entities": ["Margaret Thatcher"],
        "editorial_guidance": {"desired_first_impression": f"A clear immediate visual about {keyword}.", "historical_requirements": ["period accuracy"], "must_be_visually_dominant": [keyword], "must_not_dominate": ["generic symbols"], "common_visual_mistakes": ["anachronism"]},
    }


def packet_set():
    keywords = ["tax economy", "socialism free enterprise", "liberty responsibility", "British nation patriotism", "Soviet foreign NATO", "leader leadership decision", "lady turn attack", "principle democracy law", "Falkland Argentina", "idea choice truth"]
    return {f"{i:064x}": packet(f"{i:064x}", f"Distinct quotation number {i} concerning", value) for i, value in enumerate(keywords, 1)}


def test_deterministic_representative_selection_and_exclusions():
    packets = packet_set()
    first = select_packets(packets, set())
    second = select_packets(packets, set())
    assert [x["quote_id"] for x in first] == [x["quote_id"] for x in second]
    assert len({x["category"] for x in first}) == 10
    with pytest.raises(RuntimeError):
        select_packets(packets, {first[0]["quote_id"]})


def test_prompt_parity_hash_and_no_secret():
    prompt = canonical_prompt(next(iter(packet_set().values())))
    grok = provider_payload("grok", prompt)
    openai = provider_payload("openai", prompt)
    assert grok["prompt"].encode() == openai["prompt"].encode()
    assert "API_KEY" not in prompt
    assert "do not render this text" in prompt


def test_prepare_blinding_is_stable_and_request_names_do_not_leak(tmp_path):
    research = tmp_path / "research"; research.mkdir()
    (research / "research_packets.json").write_text(json.dumps({"items": packet_set()}))
    trial = tmp_path / "trial"
    prepare_trial(research, trial)
    first = json.loads((trial / "review/blind_map.json").read_text())
    prepare_trial(research, trial)
    assert first == json.loads((trial / "review/blind_map.json").read_text())
    assert all(set(value) == {"A", "B"} for value in first["assignments"].values())


class Response:
    status_code = 200
    text = ""
    def __init__(self, payload=None, content=b""):
        self.payload, self.content = payload, content
    def json(self): return self.payload
    def raise_for_status(self): return None


class Session:
    def __init__(self, image): self.image, self.posts = image, []
    def post(self, url, **kwargs):
        self.posts.append((url, kwargs["json"]))
        if "x.ai" in url: return Response({"data": [{"url": "https://image.invalid/x.png"}], "usage": {"cost_in_usd_ticks": 1000000000}})
        return Response({"data": [{"b64_json": base64.b64encode(self.image).decode()}], "usage": {}})
    def get(self, url, **kwargs): return Response(content=self.image)


def png_bytes():
    import io
    out = io.BytesIO(); Image.new("RGB", (32, 32), "red").save(out, "PNG"); return out.getvalue()


def test_generation_requires_exact_flag_ceiling_and_resumes(tmp_path, monkeypatch):
    research = tmp_path / "research"; research.mkdir(); (research / "research_packets.json").write_text(json.dumps({"items": packet_set()}))
    trial = tmp_path / "trial"; prepare_trial(research, trial)
    monkeypatch.setenv("XAI_API_KEY", "test-x"); monkeypatch.setenv("OPENAI_API_KEY", "test-o")
    session = Session(png_bytes())
    with pytest.raises(RuntimeError): generate_trial(trial, 999, session=session, sleep=lambda _: None)
    state = generate_trial(trial, MAX_COST_USD, session=session, sleep=lambda _: None)
    assert len(state["items"]) == 20 and len(session.posts) == 20
    generate_trial(trial, MAX_COST_USD, session=session, sleep=lambda _: None)
    assert len(session.posts) == 20


def test_report_calculation(tmp_path):
    research = tmp_path / "research"; research.mkdir(); (research / "research_packets.json").write_text(json.dumps({"items": packet_set()}))
    trial = tmp_path / "trial"; manifest = prepare_trial(research, trial)
    blind = json.loads((trial / "review/blind_map.json").read_text())["assignments"]
    decisions = {}
    for index, row in enumerate(manifest["items"]):
        decisions[row["quote_id"]] = {"choice": "A" if index < 4 else "B" if index < 7 else "equal" if index < 9 else "neither", "reasons": [], "note": ""}
    (trial / "review/decisions.json").write_text(json.dumps({"items": decisions}))
    (trial / "generation_state.json").write_text(json.dumps({"known_cost_usd": {"grok": 1, "openai": .13}}))
    report_results(trial)
    results = json.loads((trial / "review/review_results.json").read_text())
    assert sum(results["counts"].values()) == 10
    assert results["counts"]["equal"] == 2 and results["counts"]["neither"] == 1


def test_no_production_paths_are_written(tmp_path):
    research = tmp_path / "research"; research.mkdir(); (research / "research_packets.json").write_text(json.dumps({"items": packet_set()}))
    prepare_trial(research, tmp_path / "trial")
    assert not (tmp_path / "mrsMThatcher2.py").exists()
