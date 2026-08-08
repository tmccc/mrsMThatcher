from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import mrsMThatcher2 as bot
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = PROJECT_DIR / "deploy" / "systemd-user"


def test_launcher_and_service_use_the_same_canonical_bot_script() -> None:
    launcher = (PROJECT_DIR / "runMrsMThatcher2").read_text(encoding="utf-8")
    main = (SYSTEMD_DIR / "mrsMThatcher.service").read_text(encoding="utf-8")

    assert launcher.index('source "$ENV_FILE"') < launcher.index("\n  BOT_SCRIPT=")
    assert 'BOT_SCRIPT=${MRS_BOT_SCRIPT:-"$WORK_DIR/mrsMThatcher2.py"}' in launcher
    assert 'BOT_SCRIPT="$WORK_DIR/mrsMThatcher2.py"' in launcher
    assert "MRS_BOT_SCRIPT is test-only" in launcher
    assert "/usr/local/bin/mrsMThatcher2.py" not in launcher

    exec_start = next(line for line in main.splitlines() if line.startswith("ExecStart="))
    assert exec_start == "ExecStart=/disks/disk1/etc/mrsMThatcher/runMrsMThatcher2"
    assert "/usr/local/bin/runMrsMThatcher2" not in main
    preflight = next(line for line in main.splitlines() if line.startswith("ExecStartPre="))
    assert "-x /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2" in preflight
    assert "-r /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py" in preflight
    assert "-x /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py" in preflight
    exec_stop = next(line for line in main.splitlines() if line.startswith("ExecStop="))
    assert '"^python3 /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py$"' in exec_stop
    assert "/usr/local/bin/mrsMThatcher2.py" not in main


def test_canonical_user_units_cover_live_services_without_secrets() -> None:
    main = (SYSTEMD_DIR / "mrsMThatcher.service").read_text(encoding="utf-8")
    analytics = (SYSTEMD_DIR / "mrs-engagement-analytics.service").read_text(encoding="utf-8")
    timer = (SYSTEMD_DIR / "mrs-engagement-analytics.timer").read_text(encoding="utf-8")
    shadow_health = (SYSTEMD_DIR / "mrs-semantic-veto-shadow-health.service").read_text(encoding="utf-8")
    shadow_timer = (SYSTEMD_DIR / "mrs-semantic-veto-shadow-health.timer").read_text(encoding="utf-8")

    assert "ExecStart=/disks/disk1/etc/mrsMThatcher/runMrsMThatcher2" in main
    assert "Restart=on-failure" in main
    assert "KillMode=control-group" in main
    assert "StandardOutput=journal" in main
    assert "StandardError=journal" in main
    assert "UMask=0077" in main
    assert "MrsMThatcher project or wrapper unavailable after 120 seconds" in main
    assert "User=" not in main

    assert "mrs_engagement_analytics.py scheduled-run" in analytics
    assert "ReadWritePaths=/disks/disk1/etc/mrsMThatcher/engagement_analytics" in analytics
    assert "ProtectSystem=strict" in analytics
    assert "OnCalendar=*:0/15" in timer

    assert "semantic_veto_shadow_health.py" in shadow_health
    assert "Semantic-veto health inputs unavailable after 120 seconds" in shadow_health
    assert "RestrictAddressFamilies=AF_UNIX" in shadow_health
    assert "ReadWritePaths=/home/tonym/.local/state/mrsMThatcher/semantic-veto-health" in shadow_health
    assert "OnCalendar=*-*-* 23:35:00 Europe/London" in shadow_timer
    assert "Persistent=true" in shadow_timer
    assert "AccuracySec=1s" in shadow_timer

    combined = main + analytics + timer + shadow_health + shadow_timer
    assert not re.search(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*=\s*\S+", combined)


def test_user_unit_installer_prepares_and_gates_scheduled_tasks() -> None:
    installer = (SYSTEMD_DIR / "install.sh").read_text(encoding="utf-8")

    assert "systemctl --user daemon-reload" in installer
    assert "mv -f --" in installer
    assert "cmp -s --" in installer
    assert "ln -s" not in installer
    assert 'install -d -m 0700 -- "${SHADOW_HEALTH_DIR}" "${SHADOW_HEALTH_DIR}/history"' in installer
    assert installer.index("prepare_scheduled_task_state") < installer.index(
        "systemctl --user enable mrs-semantic-veto-shadow-health.timer"
    )
    assert '"${ANALYTICS_PROGRAM}" status --project-dir "${PROJECT_DIR}"' in installer
    assert "json.load(sys.stdin).get(\"initialised\") is True" in installer
    assert "systemctl --user enable mrsMThatcher.service" in installer
    assert "systemctl --user enable mrs-semantic-veto-shadow-health.timer" in installer
    assert "systemctl --user enable mrs-engagement-analytics.timer" in installer
    assert "systemctl --user disable mrs-engagement-analytics.timer" in installer
    assert "/usr/bin/python3 %q initialise --project-dir %q" in installer
    assert not re.search(r"systemctl\s+--user\s+(?:start|stop|restart|reload)\b", installer)


@pytest.mark.parametrize("analytics_initialised", [False, True])
def test_user_unit_installer_gates_analytics_without_blocking_other_units(
    tmp_path: Path,
    analytics_initialised: bool,
) -> None:
    project = tmp_path / "project"
    deployed = project / "deploy" / "systemd-user"
    deployed.mkdir(parents=True)
    for source in SYSTEMD_DIR.iterdir():
        if source.is_file():
            shutil.copy2(source, deployed / source.name)
    analytics_program = project / "mrs_engagement_analytics.py"
    analytics_program.write_text(
        "import json, os, sys\n"
        "assert sys.argv[1:] == ['status', '--project-dir', os.environ['EXPECTED_PROJECT']]\n"
        "print(json.dumps({'initialised': os.environ['ANALYTICS_INITIALISED'] == '1'}))\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "systemd-analyze").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$SYSTEMCTL_CALLS\"\n"
        "if [[ \"$*\" == *'enable mrs-semantic-veto-shadow-health.timer'* ]]; then\n"
        "  [[ $(stat -c %a \"$XDG_STATE_HOME/mrsMThatcher/semantic-veto-health\") == 700 ]]\n"
        "  [[ $(stat -c %a \"$XDG_STATE_HOME/mrsMThatcher/semantic-veto-health/history\") == 700 ]]\n"
        "fi\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "systemd-analyze", fake_bin / "systemctl"):
        command.chmod(0o755)
    calls = tmp_path / "systemctl.calls"
    env = os.environ.copy()
    env.update(
        {
            "ANALYTICS_INITIALISED": "1" if analytics_initialised else "0",
            "EXPECTED_PROJECT": str(project),
            "HOME": str(tmp_path / "home"),
            "MRS_SEMANTIC_VETO_HEALTH_DIR": str(
                tmp_path / "state" / "mrsMThatcher" / "semantic-veto-health"
            ),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "SYSTEMCTL_CALLS": str(calls),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
    )

    result = subprocess.run(
        [str(deployed / "install.sh"), "--install"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    systemctl_calls = calls.read_text(encoding="utf-8")
    assert "--user enable mrsMThatcher.service" in systemctl_calls
    assert "--user enable mrs-semantic-veto-shadow-health.timer" in systemctl_calls
    if analytics_initialised:
        assert "--user enable mrs-engagement-analytics.timer" in systemctl_calls
        assert "--user disable mrs-engagement-analytics.timer" not in systemctl_calls
    else:
        assert "--user disable mrs-engagement-analytics.timer" in systemctl_calls
        assert "--user enable mrs-engagement-analytics.timer" not in systemctl_calls
        assert (
            f"run: /usr/bin/python3 {analytics_program} initialise --project-dir {project}"
            in result.stdout
        )


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
