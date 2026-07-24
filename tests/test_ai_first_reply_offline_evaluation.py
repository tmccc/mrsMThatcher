import json
from pathlib import Path

from tools.evaluate_ai_first_reply_strategy import evaluate, render


ROOT = Path(__file__).resolve().parents[1]


def test_offline_evaluation_covers_fixtures_history_and_cost_without_network(
    monkeypatch,
    tmp_path,
) -> None:
    def forbidden_socket(*_args, **_kwargs):
        raise AssertionError("offline reply evaluation must not use the network")

    monkeypatch.setattr("socket.create_connection", forbidden_socket)
    monkeypatch.setattr("socket.socket.connect", forbidden_socket)
    monkeypatch.setattr("socket.socket.connect_ex", forbidden_socket)
    state_path = tmp_path / "bot_state.json"
    state_path.write_text(
        json.dumps(
            {
                "reply_strategy_history": [
                    {
                        "target_id": str(10_000 + index),
                        "reply_text": "Thank you for your comment.",
                        "mode": "social",
                    }
                    for index in range(25)
                ]
            }
        ),
        encoding="utf-8",
    )
    result = evaluate(ROOT, state_path=state_path)

    assert result["network_calls"] == 0
    assert result["model_calls"] == 0
    assert result["adversarial_fixture"]["case_count"] == 14
    assert result["adversarial_fixture"]["expected_reject_count"] == 14
    assert result["valid_fixture"]["case_count"] == 6
    assert result["valid_fixture"]["all_modes_valid"] is True
    assert result["saved_historical_replies"]["history_count"] >= 25
    assert result["local_retrieval"]["query_count"] == 5
    assert "10,000,000,000 ticks" in result["estimated_api_cost"]["basis"]
    median = result["estimated_api_cost"]["median_historical_call_usd"]
    if median is not None:
        assert 0 < median < 0.1
    assert "no model was contacted" in render(result).lower()
