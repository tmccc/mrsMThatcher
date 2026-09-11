"""Retired generated-image features do no current runtime inventory work."""

import json

import mrs_log_digest as digest


def test_cli_skips_retired_asset_scans_but_keeps_historical_spacing(monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("retired runtime feature must not inspect generated assets")

    for helper in (
        "generated_pool_health_snapshot", "generated_post_rate_history",
        "load_runway_config", "generated_pool_runway", "generated_image_utilisation",
    ):
        monkeypatch.setattr(digest, helper, forbidden)
    log = tmp_path / "mrsMThatcher.log"
    log.write_text(
        "2026-09-11 01:00:00 INFO fixture:1 - "
        "GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=true "
        "original_posts_since_generated=2 required=2\n"
    )
    (tmp_path / "mrsMThatcher.local.json").write_text(json.dumps({
        "MAX_AUTO_REPLIES_PER_DAY": 48,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
    }))

    assert digest.main([
        "--project-dir", str(tmp_path), "--since", "2026-09-11 00:00:00",
        "--until", "2026-09-11 02:00:00", "--no-state", "--json", str(log),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    for section in (
        "generated_image_pool_health", "generated_image_post_rates",
        "generated_image_pool_runway", "generated_image_utilisation",
    ):
        assert report[section]["status"] == "retired"
        assert report[section]["available"] is False
    assert report["generated_image_spacing"]["events"]
    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN" not in report["latest_config"]
    rendered = digest.render_markdown(report)
    assert "## Historical generated image spacing" in rendered
    assert "retained historical observations" in rendered
    assert "## Generated image pool health" not in rendered
    assert "## Generated image utilisation" not in rendered
    assert report["state_updated"] is False
    assert not (tmp_path / ".mrs_log_digest_state.json").exists()
