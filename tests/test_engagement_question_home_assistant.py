from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import jinja2
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = (
    PROJECT_ROOT
    / "deploy"
    / "home-assistant"
    / "mrs_m_thatcher_engagement_question.yaml"
)
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def template_environment() -> jinja2.Environment:
    environment = jinja2.Environment(autoescape=False)
    environment.tests["match"] = lambda value, pattern: bool(
        re.match(pattern, str(value))
    )
    environment.globals["now"] = lambda: NOW
    environment.globals["as_timestamp"] = lambda value: value.timestamp()
    return environment


def render_boolean(template: str, **values) -> bool:
    rendered = template_environment().from_string(template).render(**values)
    assert rendered.strip().casefold() in {"true", "false"}
    return rendered.strip().casefold() == "true"


def treatment_attributes(*, published_epoch: int) -> dict:
    post_id = "2093851189048246272"
    return {
        "schema_version": 1,
        "experiment_id": "substantive-question-v1",
        "plan_sha256": "a" * 64,
        "post_id": post_id,
        "pair_id": "pair-" + "b" * 24,
        "treatment_number": 7,
        "target_treatment_count": 30,
        "published_epoch": published_epoch,
        "quote_excerpt": "There is no liberty without responsibility.",
        "question": "What responsibility does liberty require?",
        "post_url": f"https://x.com/MrsMThatcher/status/{post_id}",
    }


def test_home_assistant_sensor_and_notification_templates_are_behavioral() -> None:
    package = yaml.safe_load(PACKAGE_PATH.read_text(encoding="utf-8"))
    sensor = package["command_line"][0]["sensor"]
    automation = package["automation"][0]
    environment = template_environment()

    assert sensor["unique_id"] == "mrs_m_thatcher_engagement_question"
    assert sensor["command"] == (
        "cat /config/.runtime/mrs_m_thatcher_engagement_question.json"
    )
    sensor_template = environment.from_string(sensor["value_template"])
    attributes = treatment_attributes(published_epoch=int(NOW.timestamp()) - 60)
    assert sensor_template.render(value_json=attributes).strip() == attributes["post_id"]
    assert sensor_template.render(value_json={"post_id": "invalid"}).strip() == (
        "unavailable"
    )

    condition = automation["conditions"][0]["value_template"]
    trigger = SimpleNamespace(
        from_state=SimpleNamespace(state="unavailable", attributes={}),
        to_state=SimpleNamespace(
            state=attributes["post_id"],
            attributes=attributes,
        ),
    )
    assert render_boolean(condition, trigger=trigger) is True

    stale = dict(attributes, published_epoch=int(NOW.timestamp()) - 601)
    stale_trigger = SimpleNamespace(
        from_state=trigger.from_state,
        to_state=SimpleNamespace(state=attributes["post_id"], attributes=stale),
    )
    assert render_boolean(condition, trigger=stale_trigger) is False

    same_post_trigger = SimpleNamespace(
        from_state=SimpleNamespace(state=attributes["post_id"], attributes={}),
        to_state=trigger.to_state,
    )
    assert render_boolean(condition, trigger=same_post_trigger) is False

    invalid_url = dict(attributes, post_url="https://example.invalid/post")
    invalid_trigger = SimpleNamespace(
        from_state=trigger.from_state,
        to_state=SimpleNamespace(
            state=attributes["post_id"],
            attributes=invalid_url,
        ),
    )
    assert render_boolean(condition, trigger=invalid_trigger) is False

    action = automation["actions"][0]
    assert action["action"] == "notify.millie_powerwall_alert_devices"
    title = environment.from_string(action["data"]["title"]).render(trigger=trigger)
    message = environment.from_string(action["data"]["message"]).render(
        trigger=trigger
    )
    tag = environment.from_string(action["data"]["data"]["tag"]).render(
        trigger=trigger
    )
    assert "MrsMThatcher treatment post 7/30" in " ".join(title.split())
    assert attributes["quote_excerpt"] in message
    assert attributes["question"] in message
    assert attributes["post_url"] in message
    assert tag.strip() == f"mrs_m_thatcher_treatment_{attributes['post_id']}"
