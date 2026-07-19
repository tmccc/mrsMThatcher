from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import mrsMThatcher2 as bot


PROJECT_DIR = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = PROJECT_DIR / "deploy" / "systemd-user"


def test_canonical_user_units_cover_live_services_without_secrets() -> None:
    main = (SYSTEMD_DIR / "mrsMThatcher.service").read_text(encoding="utf-8")
    analytics = (SYSTEMD_DIR / "mrs-engagement-analytics.service").read_text(encoding="utf-8")
    timer = (SYSTEMD_DIR / "mrs-engagement-analytics.timer").read_text(encoding="utf-8")

    assert "ExecStart=/usr/local/bin/runMrsMThatcher2" in main
    assert "Restart=on-failure" in main
    assert "KillMode=control-group" in main
    assert "MrsMThatcher project or wrapper unavailable after 120 seconds" in main
    assert "User=" not in main

    assert "mrs_engagement_analytics.py scheduled-run" in analytics
    assert "ReadWritePaths=/disks/disk1/etc/mrsMThatcher/engagement_analytics" in analytics
    assert "ProtectSystem=strict" in analytics
    assert "OnCalendar=*:0/15" in timer

    combined = main + analytics + timer
    assert not re.search(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*=\s*\S+", combined)


def test_user_unit_installer_cannot_activate_or_restart_services() -> None:
    installer = (SYSTEMD_DIR / "install.sh").read_text(encoding="utf-8")

    assert "systemctl --user daemon-reload" in installer
    assert "mv -f --" in installer
    assert "cmp -s --" in installer
    assert "ln -s" not in installer
    assert not re.search(r"systemctl\s+--user\s+(?:enable|disable|start|stop|restart|reload)\b", installer)


def test_local_config_example_covers_current_optional_selection_features() -> None:
    config = json.loads((PROJECT_DIR / "mrsMThatcher.local.example.json").read_text(encoding="utf-8"))
    required = {
        "ENABLE_GENERATED_IMAGE_POOL",
        "GENERATED_IMAGE_DIR",
        "GENERATED_IMAGE_GLOB",
        "GENERATED_IMAGE_ANALYSIS_FILE",
        "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
        "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING",
        "ORIGINAL_EDITORIAL_ANALYSIS_FILE",
        "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
        "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
        "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING",
        "ENABLE_GENERATED_IDENTITY_POLICY_SCORING",
        "GENERATED_IDENTITY_AUDIT_FILE",
        "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
        "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
    }

    assert required <= config.keys()
    assert set(config) <= set(bot.LOCAL_CONFIG_ALLOWED_KEYS)
    assert config["ENABLE_GENERATED_IMAGE_POOL"] is False
    assert config["ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING"] is False
    assert config["ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING"] is False
    assert config["ENABLE_GENERATED_IDENTITY_POLICY_SCORING"] is False
    assert config["quote_image_semantic_veto"]["enabled"] is False
    assert "material_veto_v3_shadow_manifest.json" in config["quote_image_semantic_veto"]["manifest_path"]


def test_local_config_example_is_accepted_as_one_atomic_override(monkeypatch) -> None:
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", PROJECT_DIR / "mrsMThatcher.local.example.json")
    before = {
        key: copy.deepcopy(getattr(bot, key))
        for key in bot.LOCAL_CONFIG_ALLOWED_KEYS
        if hasattr(bot, key)
    }

    try:
        bot.apply_local_config()
    finally:
        for key, value in before.items():
            setattr(bot, key, value)
