import base64
import io
import json

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


REAL_RUN = "semantic_alignment_research/quote_research_full_001"


def test_real_eligible_corpus_is_626_and_excludes_six():
    from pathlib import Path
    packets, unresolved, digest = load_corpus(Path(REAL_RUN))
    assert len(packets) == 626 and len(unresolved) == 6
    assert not (set(packets) & unresolved)
    assert len(digest) == 64


def test_prompt_parity_and_quality_only_difference():
    packet = json.loads(open(f"{REAL_RUN}/research_packets.json").read())["items"]
    prompt = canonical_prompt(next(iter(packet.values())))
    medium, high = request_payload(prompt, "medium"), request_payload(prompt, "high")
    assert medium["prompt"].encode() == high["prompt"].encode()
    assert payload_difference_is_quality_only(medium, high)
    assert medium["model"] == high["model"] == MODEL


def test_prepare_is_deterministic_and_proves_exclusion(tmp_path):
    from pathlib import Path
    run = Path(REAL_RUN)
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


def test_generation_ceiling_one_each_and_resume(tmp_path, monkeypatch):
    from pathlib import Path
    trial=tmp_path/"trial";prepare_trial(Path(REAL_RUN),trial)
    pf=json.loads((trial/"reports/preflight.json").read_text());monkeypatch.setenv("OPENAI_API_KEY","test")
    session=Session(image_bytes())
    with pytest.raises(RuntimeError):generate_trial(trial,999,session=session,sleep=lambda _:None)
    state=generate_trial(trial,pf["required_exact_confirmation_usd"],session=session,sleep=lambda _:None)
    assert len(state["items"])==20 and len(session.calls)==20
    assert {x["quality"] for x in session.calls}=={"medium","high"}
    generate_trial(trial,pf["required_exact_confirmation_usd"],session=session,sleep=lambda _:None)
    assert len(session.calls)==20


def test_no_live_call_without_key(tmp_path, monkeypatch):
    from pathlib import Path
    trial=tmp_path/"trial";prepare_trial(Path(REAL_RUN),trial);monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    pf=json.loads((trial/"reports/preflight.json").read_text())
    with pytest.raises(RuntimeError,match="OPENAI_API_KEY"):generate_trial(trial,pf["required_exact_confirmation_usd"],session=Session(image_bytes()))


def test_no_production_write(tmp_path):
    from pathlib import Path
    prepare_trial(Path(REAL_RUN),tmp_path/"trial")
    assert not (tmp_path/"mrsMThatcher2.py").exists()
