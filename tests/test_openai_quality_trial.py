import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from semantic_alignment.image_provider_trial import canonical_prompt
from semantic_alignment.openai_quality_trial import (
    MODEL,
    generate_trial,
    load_corpus,
    payload_difference_is_quality_only,
    prepare_trial,
    request_payload,
)
from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)


@pytest.fixture(scope="module")
def research_run(historical_corpus_root: Path) -> Path:
    """The saved quality experiment requires its original 627/5 corpus."""
    return historical_corpus_root / RESEARCH_RELATIVE


def test_original_quality_trial_corpus_has_627_completed_and_five_unresolved(research_run):
    packets, unresolved, digest = load_corpus(research_run)
    assert len(packets) == 627 and len(unresolved) == 5
    assert not (set(packets) & unresolved)
    saved_ids = json.loads((research_run / "research_packets.json").read_text())["items"]
    assert set(packets) == set(saved_ids)
    assert digest == hashlib.sha256("\n".join(sorted(saved_ids)).encode()).hexdigest()


def test_prompt_parity_and_quality_only_difference(research_run):
    packet = json.loads((research_run / "research_packets.json").read_text())["items"]
    prompt = canonical_prompt(next(iter(packet.values())))
    medium, high = request_payload(prompt, "medium"), request_payload(prompt, "high")
    assert medium["prompt"].encode() == high["prompt"].encode()
    assert payload_difference_is_quality_only(medium, high)
    assert medium["model"] == high["model"] == MODEL


def test_prepare_is_deterministic_and_proves_exclusion(tmp_path, research_run):
    run = research_run
    one, two = tmp_path / "one", tmp_path / "two"
    first = prepare_trial(run, one); second = prepare_trial(run, two)
    assert [x["quote_id"] for x in first["items"]] == [x["quote_id"] for x in second["items"]]
    assert first["selected_quote_list_sha256"] == second["selected_quote_list_sha256"]
    assert all(x["completed_packet_proof"] and x["unresolved_exclusion_proof"] for x in first["items"])


class Response:
    status_code = 200
    text = ""
    def __init__(self, image): self.image = image
    def json(self): return {"id": "test", "data": [{"b64_json": base64.b64encode(self.image).decode()}], "usage": {}}


class Session:
    def __init__(self, image): self.image, self.calls = image, []
    def post(self, url, **kwargs): self.calls.append(kwargs["json"]); return Response(self.image)


def image_bytes():
    out=io.BytesIO();Image.new("RGB",(24,24),"blue").save(out,"PNG");return out.getvalue()


def test_generation_ceiling_one_each_and_resume(tmp_path, monkeypatch, research_run):
    trial=tmp_path/"trial";prepare_trial(research_run,trial)
    pf=json.loads((trial/"reports/preflight.json").read_text());monkeypatch.setenv("OPENAI_API_KEY","test")
    session=Session(image_bytes())
    with pytest.raises(RuntimeError):generate_trial(trial,999,session=session,sleep=lambda _:None)
    state=generate_trial(trial,pf["required_exact_confirmation_usd"],session=session,sleep=lambda _:None)
    assert len(state["items"])==20 and len(session.calls)==20
    assert {x["quality"] for x in session.calls}=={"medium","high"}
    generate_trial(trial,pf["required_exact_confirmation_usd"],session=session,sleep=lambda _:None)
    assert len(session.calls)==20


def test_no_live_call_without_key(tmp_path, monkeypatch, research_run):
    trial=tmp_path/"trial";prepare_trial(research_run,trial);monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    pf=json.loads((trial/"reports/preflight.json").read_text())
    with pytest.raises(RuntimeError,match="OPENAI_API_KEY"):generate_trial(trial,pf["required_exact_confirmation_usd"],session=Session(image_bytes()))


def test_no_production_write(tmp_path, research_run):
    prepare_trial(research_run,tmp_path/"trial")
    assert not (tmp_path/"mrsMThatcher2.py").exists()
