from __future__ import annotations

import json
import threading
from pathlib import Path

import requests

from semantic_alignment.quote_research_corpus import CorpusRunner, build_corpus_manifest


from tests.helpers.quote_research import FakeDeveloper, FakeVertex, manifest, packet, response, row

ROOT = Path(__file__).resolve().parents[1]


def http_429() -> requests.HTTPError:
    res = requests.Response(); res.status_code = 429
    res._content = b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"daily quota"}}'
    return requests.HTTPError("429", response=res)


class Tracker:
    def __init__(self):
        self.active = self.maximum = self.entries = 0
        self.lock = threading.Lock()
        self.first_pair = threading.Barrier(2)

    def enter(self):
        with self.lock:
            self.entries += 1
            entry_number = self.entries
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        if entry_number <= 2:
            self.first_pair.wait(timeout=5)

    def leave(self):
        with self.lock:
            self.active -= 1


def runner(tmp_path, records, developer, vertex, **kwargs):
    return CorpusRunner(tmp_path, records, developer, vertex, kwargs.pop("developer_limit", 10),
                        kwargs.pop("vertex_limit", 10), kwargs.pop("combined_limit", 15),
                        sleep=lambda _: None, **kwargs)


def test_immutable_632_manifest_and_duplicate_occurrences():
    source = json.loads((ROOT / "thatcher_quote_research_project/quote_manifest.json").read_text())
    built = build_corpus_manifest(source)
    assert built["record_count"] == 632 and built["source_occurrence_count"] == 633
    assert sum(x["duplicate_occurrence_count"] for x in built["records"]) == 633
    assert any(x["duplicate_occurrence_count"] == 2 for x in built["records"])


def test_resume_skips_valid_packet(tmp_path):
    data = manifest(1); rec = data["records"][0]
    first = runner(tmp_path, data, FakeDeveloper([response(rec, "developer")]), FakeVertex([]))
    assert first.run()["valid_packets"] == 1
    dev = FakeDeveloper([])
    assert runner(tmp_path, data, dev, FakeVertex([])).run()["valid_packets"] == 1
    assert dev.calls == 0


def test_timeout_retries_once_then_becomes_permanent(tmp_path):
    data = manifest(1)
    dev = FakeDeveloper([requests.ReadTimeout("read timeout"), requests.ReadTimeout("read timeout")])
    status = runner(tmp_path, data, dev, FakeVertex([])).run()
    assert dev.calls == 2 and status["permanent_failures"] == 1
    attempts = (tmp_path / "attempts.jsonl").read_text()
    assert "read_timeout" in attempts and "exhausted_transient_failure" in json.dumps(json.loads((tmp_path/"permanent_failures.json").read_text()))


def test_raw_response_and_schema_repair_are_preserved(tmp_path):
    data = manifest(1); rec = data["records"][0]
    dev = FakeDeveloper([response(rec, "developer", malformed=True), response(rec, "developer")])
    status = runner(tmp_path, data, dev, FakeVertex([])).run()
    assert status["valid_packets"] == 1 and dev.calls == 2
    raw = tmp_path / "raw_responses" / rec["quote_id"]
    assert (raw / "developer_api_attempt_1.json").exists()
    rows = [json.loads(x) for x in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert any(x.get("repair_attempt") for x in rows)


def test_permanent_failure_persists_and_reset_preserves_audit(tmp_path):
    data = manifest(1); rec = data["records"][0]
    dev = FakeDeveloper([ValueError("bad request")])
    first = runner(tmp_path, data, dev, FakeVertex([])); first.run()
    assert runner(tmp_path, data, FakeDeveloper([]), FakeVertex([])).run()["permanent_failures"] == 1
    resumed = runner(tmp_path, data, FakeDeveloper([response(rec, "developer")]), FakeVertex([]))
    resumed.reset_permanent(rec["quote_id"])
    assert resumed.run()["valid_packets"] == 1
    assert json.loads((tmp_path/"permanent_failures.json").read_text())["reset_audit"]


def test_graceful_stop_makes_no_call(tmp_path):
    data = manifest(1); dev = FakeDeveloper([])
    work = runner(tmp_path, data, dev, FakeVertex([])); work.request_stop("SIGINT")
    status = work.run()
    assert dev.calls == 0 and status["pending"] == 1


def test_cost_ceiling_stops_before_call(tmp_path):
    data = manifest(1); dev = FakeDeveloper([])
    status = runner(tmp_path, data, dev, FakeVertex([]), developer_limit=.0001,
                    vertex_limit=.0001, combined_limit=.0001).run()
    assert dev.calls == 0 and status["remaining_nonterminal"] == 1


def test_first_429_then_success_does_not_pause(tmp_path):
    data = manifest(1); rec = data["records"][0]
    dev = FakeDeveloper([http_429(), response(rec, "developer")])
    status = runner(tmp_path, data, dev, FakeVertex([])).run()
    assert status["valid_packets"] == 1 and not status["developer_paused"] and dev.calls == 2


def test_two_429s_pause_and_route_trigger_and_later_cases_to_vertex(tmp_path):
    data = manifest(3)
    dev = FakeDeveloper([http_429(), http_429()])
    by_id = {rec["quote_id"]: rec for rec in data["records"]}
    def routed(prompt):
        rec = next(value for quote_id, value in by_id.items() if quote_id in prompt)
        return response(rec, "vertex")
    vertex = FakeVertex([routed, routed, routed])
    status = runner(tmp_path, data, dev, vertex, developer_concurrency=1, vertex_concurrency=2).run()
    assert dev.calls == 2 and vertex.calls == 3
    assert status["developer_paused"] and status["direct_to_vertex_count"] == 2
    resumed_dev = FakeDeveloper([])
    runner(tmp_path, data, resumed_dev, FakeVertex([])).run()
    assert resumed_dev.calls == 0


def test_bounded_provider_concurrency(tmp_path):
    data = manifest(3); tracker = Tracker()
    by_id = {rec["quote_id"]: rec for rec in data["records"]}
    def routed(prompt):
        rec = next(value for quote_id, value in by_id.items() if quote_id in prompt)
        return response(rec, "developer")
    status = runner(tmp_path, data, FakeDeveloper([routed] * 3, tracker), FakeVertex([]),
                    developer_concurrency=2).run()
    assert status["valid_packets"] == 3 and tracker.maximum == 2


def test_completed_worker_slot_refills_while_other_call_is_slow(tmp_path):
    data = manifest(3)
    third_started = threading.Event()
    by_id = {record["quote_id"]: record for record in data["records"]}

    def routed(prompt):
        record = next(
            record for quote_id, record in by_id.items() if quote_id in prompt
        )
        if record == data["records"][0]:
            return slow(record)
        if record == data["records"][2]:
            third_started.set()
        return response(record, "developer")

    def slow(record):
        # Full-suite I/O load can delay the completed worker's persistence
        # before the executor refills its slot. Retain a bounded deadlock check
        # without assuming that scheduling and fsync complete within one second.
        assert third_started.wait(30)
        return response(record, "developer")

    # Every worker receives the same prompt-keyed callback, so the assertion
    # does not depend on thread scheduling or shared-list consumption order.
    dev = FakeDeveloper([routed, routed, routed])
    status = runner(tmp_path, data, dev, FakeVertex([]), developer_concurrency=2).run()
    assert status["valid_packets"] == 3 and third_started.is_set()
