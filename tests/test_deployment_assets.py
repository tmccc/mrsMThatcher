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

    assert "readonly WORK_DIR=" in launcher
    assert "readonly ENV_FILE=" in launcher
    assert 'readonly CANONICAL_BOT_SCRIPT="$WORK_DIR/mrsMThatcher2.py"' in launcher
    assert launcher.index("readonly INHERITED_TEST_MODE=") < launcher.index('source "$ENV_FILE"')
    assert launcher.index("readonly INHERITED_WORK_DIR=") < launcher.index('source "$ENV_FILE"')
    assert launcher.index("readonly INHERITED_ENV_FILE=") < launcher.index('source "$ENV_FILE"')
    assert launcher.index("readonly INHERITED_BOT_SCRIPT=") < launcher.index('source "$ENV_FILE"')
    assert "readonly WORK_DIR=/disks/disk1/etc/mrsMThatcher" in launcher
    assert "readonly ENV_FILE=/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env" in launcher
    assert 'readonly BOT_SCRIPT="$CANONICAL_BOT_SCRIPT"' in launcher
    assert "MRS_WORK_DIR is test-only" in launcher
    assert "MRS_ENV_FILE is test-only" in launcher
    assert "MRS_BOT_SCRIPT is test-only" in launcher
    assert "the environment file cannot authorise test hooks" in launcher
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
    openai_cost = (SYSTEMD_DIR / "mrs-openai-cost-cache.service").read_text(encoding="utf-8")
    openai_timer = (SYSTEMD_DIR / "mrs-openai-cost-cache.timer").read_text(encoding="utf-8")
    prospective = (SYSTEMD_DIR / "mrs-prospective-conversations.service").read_text(
        encoding="utf-8"
    )
    prospective_timer = (SYSTEMD_DIR / "mrs-prospective-conversations.timer").read_text(
        encoding="utf-8"
    )

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
    assert "--output-dir %h/.local/state/mrsMThatcher/semantic-veto-health" in shadow_health
    assert "ReadWritePaths=%h/.local/state/mrsMThatcher/semantic-veto-health" in shadow_health
    assert "OnCalendar=*-*-* 23:35:00 Europe/London" in shadow_timer
    assert "Persistent=true" in shadow_timer
    assert "AccuracySec=1s" in shadow_timer

    assert "GET" not in openai_cost
    assert "source /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env" in openai_cost
    assert "openai_cost_cache.py update" in openai_cost
    assert "OPENAI_ADMIN_API_KEY=" not in openai_cost
    assert "ProtectSystem=strict" in openai_cost
    assert "ProtectHome=read-only" in openai_cost
    assert "ReadWritePaths=%h/.local/state/mrsMThatcher/openai-costs" in openai_cost
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in openai_cost
    assert "UMask=0077" in openai_cost
    assert "OnCalendar=*:0/30" in openai_timer
    assert "Persistent=true" in openai_timer
    assert "RandomizedDelaySec=60" in openai_timer
    assert "Unit=mrs-openai-cost-cache.service" in openai_timer

    expected_exec = (
        "ExecStart=/usr/bin/python3 "
        "/disks/disk1/etc/mrsMThatcher/tools/extract_prospective_conversations.py "
        "scan --project-dir /disks/disk1/etc/mrsMThatcher "
        "--output-root /disks/disk1/research/mrsMThatcher-prospective-conversations-v3 "
        "--prospective-start 2026-08-24T15:08:39Z --quiescence-hours 48"
    )
    assert expected_exec in prospective
    assert (
        "/disks/disk1/research/mrsMThatcher-prospective-conversations "
        not in prospective
    )
    assert "ConditionPathExists=/disks/disk1/etc/mrsMThatcher/mrsMThatcher.log" in prospective
    assert "EnvironmentFile=" not in prospective
    assert "source " not in prospective
    assert prospective.count("mrsMThatcher.env") == 1
    assert "RestrictAddressFamilies=AF_UNIX" in prospective
    assert "AF_INET" not in prospective
    read_write_lines = [
        line for line in prospective.splitlines() if line.startswith("ReadWritePaths=")
    ]
    assert read_write_lines == [
        "ReadWritePaths=/disks/disk1/research/mrsMThatcher-prospective-conversations-v3"
    ]
    assert "ProtectSystem=strict" in prospective
    assert "UMask=0077" in prospective
    assert "StandardOutput=journal" in prospective
    assert "StandardError=journal" in prospective
    assert "OnCalendar=hourly" in prospective_timer
    assert "Persistent=true" in prospective_timer
    assert "RandomizedDelaySec=5m" in prospective_timer
    assert "AccuracySec=1m" in prospective_timer
    assert "Unit=mrs-prospective-conversations.service" in prospective_timer

    combined = (
        main
        + analytics
        + timer
        + shadow_health
        + shadow_timer
        + openai_cost
        + openai_timer
        + prospective
        + prospective_timer
    )
    assert not re.search(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*=\s*\S+", combined)


def test_user_unit_installer_prepares_and_gates_scheduled_tasks() -> None:
    installer = (SYSTEMD_DIR / "install.sh").read_text(encoding="utf-8")

    assert "systemctl --user daemon-reload" in installer
    assert "mv -f --" in installer
    assert "cmp -s --" in installer
    assert "ln -s" not in installer
    assert "SOURCE_PROJECT_DIR=" in installer
    assert 'RUNTIME_PROJECT_DIR="${MRS_RUNTIME_PROJECT_DIR:-/disks/disk1/etc/mrsMThatcher}"' in installer
    assert '"${SHADOW_HEALTH_DIR}/history"' in installer
    assert '"${OPENAI_COST_DIR}"' in installer
    assert 'SHADOW_HEALTH_DIR="${HOME}/.local/state/mrsMThatcher/semantic-veto-health"' in installer
    assert 'OPENAI_COST_DIR="${HOME}/.local/state/mrsMThatcher/openai-costs"' in installer
    assert (
        'PROSPECTIVE_CONVERSATION_DIR="${MRS_PROSPECTIVE_CONVERSATION_DIR:-'
        '/disks/disk1/research/mrsMThatcher-prospective-conversations-v3}"'
        in installer
    )
    assert (
        '/disks/disk1/research/mrsMThatcher-prospective-conversations}"'
        not in installer
    )
    assert '"${PROSPECTIVE_CONVERSATION_DIR}/state"' in installer
    assert '"${PROSPECTIVE_CONVERSATION_DIR}/batches"' in installer
    assert '"${PROSPECTIVE_CONVERSATION_DIR}/review-packs"' in installer
    assert "MRS_SEMANTIC_VETO_HEALTH_DIR" not in installer
    assert '"${ANALYTICS_PROGRAM}" status --project-dir "${RUNTIME_PROJECT_DIR}"' in installer
    assert "initialise --project-dir %q" in installer
    assert installer.index("systemctl --user daemon-reload") < installer.index(
        "report_analytics_readiness ||"
    )
    systemctl_invocations = [
        line.strip()
        for line in installer.splitlines()
        if line.strip().startswith("systemctl ")
    ]
    assert systemctl_invocations == ["systemctl --user daemon-reload"]
    for unit in (
        "mrsMThatcher.service",
        "mrs-semantic-veto-shadow-health.timer",
        "mrs-engagement-analytics.timer",
    ):
        assert f"systemctl --user enable {unit}" in installer
    assert "systemctl --user enable --now mrs-openai-cost-cache.timer" in installer
    assert "systemctl --user enable --now mrs-prospective-conversations.timer" not in installer
    assert "systemctl --user start mrs-prospective-conversations.service" not in installer
    assert "left prospective conversation v3 root absent for registered rebuild" in installer
    assert "mrs-prospective-conversations.service" in installer
    assert "mrs-prospective-conversations.timer" in installer


@pytest.mark.parametrize(
    ("analytics_status", "expected_returncode"),
    [
        ("initialised", 0),
        ("uninitialised", 0),
        ("command_failure", 1),
        ("malformed", 1),
        ("non_boolean_optimized", 1),
    ],
)
def test_user_unit_installer_reports_runtime_readiness_without_activating_units(
    tmp_path: Path,
    analytics_status: str,
    expected_returncode: int,
) -> None:
    source_project = tmp_path / "source-project"
    deployed = source_project / "deploy" / "systemd-user"
    deployed.mkdir(parents=True)
    for source in SYSTEMD_DIR.iterdir():
        if source.is_file():
            shutil.copy2(source, deployed / source.name)
    runtime_project = tmp_path / "runtime-project"
    runtime_project.mkdir()
    analytics_program = runtime_project / "mrs_engagement_analytics.py"
    analytics_program.write_text(
        "import json, os, sys\n"
        "assert sys.argv[1:] == ['status', '--project-dir', os.environ['EXPECTED_PROJECT']]\n"
        "status = os.environ['ANALYTICS_STATUS']\n"
        "if status == 'command_failure': raise SystemExit(7)\n"
        "if status == 'malformed': print('{malformed')\n"
        "elif status == 'non_boolean_optimized': print(json.dumps({'initialised': 'yes'}))\n"
        "else: print(json.dumps({'initialised': status == 'initialised'}))\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "systemd-analyze").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$SYSTEMCTL_CALLS\"\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "systemd-analyze", fake_bin / "systemctl"):
        command.chmod(0o755)
    calls = tmp_path / "systemctl.calls"
    prospective_dir = tmp_path / "prospective-conversations"
    env = os.environ.copy()
    env.update(
        {
            "ANALYTICS_STATUS": analytics_status,
            "EXPECTED_PROJECT": str(runtime_project),
            "HOME": str(tmp_path / "home"),
            "MRS_RUNTIME_PROJECT_DIR": str(runtime_project),
            "MRS_PROSPECTIVE_CONVERSATION_DIR": str(prospective_dir),
            "MRS_TEST_MODE": "1",
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "SYSTEMCTL_CALLS": str(calls),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        }
    )
    env.pop("PYTHONOPTIMIZE", None)
    if analytics_status == "non_boolean_optimized":
        env["PYTHONOPTIMIZE"] = "1"

    result = subprocess.run(
        [str(deployed / "install.sh"), "--install"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stderr + result.stdout
    systemctl_calls = calls.read_text(encoding="utf-8").splitlines()
    assert systemctl_calls == ["--user daemon-reload"]
    assert not any(
        re.search(r"(?:^|\s)(?:enable|disable|start|stop|restart)(?:\s|$)", call)
        for call in systemctl_calls
    )
    target_dir = tmp_path / "config" / "systemd" / "user"
    for unit in (
        "mrsMThatcher.service",
        "mrs-engagement-analytics.service",
        "mrs-engagement-analytics.timer",
        "mrs-openai-cost-cache.service",
        "mrs-openai-cost-cache.timer",
        "mrs-prospective-conversations.service",
        "mrs-prospective-conversations.timer",
        "mrs-semantic-veto-shadow-health.service",
        "mrs-semantic-veto-shadow-health.timer",
    ):
        assert (target_dir / unit).read_bytes() == (deployed / unit).read_bytes()
    health_dir = tmp_path / "home" / ".local" / "state" / "mrsMThatcher" / "semantic-veto-health"
    assert health_dir.stat().st_mode & 0o777 == 0o700
    assert (health_dir / "history").stat().st_mode & 0o777 == 0o700
    cost_dir = tmp_path / "home" / ".local" / "state" / "mrsMThatcher" / "openai-costs"
    assert cost_dir.stat().st_mode & 0o777 == 0o700
    assert not prospective_dir.exists()
    assert "left prospective conversation v3 root absent for registered rebuild" in result.stdout
    assert "enable --now mrs-prospective-conversations.timer" not in result.stdout
    assert "start mrs-prospective-conversations.service" not in result.stdout
    assert "enable each desired unit separately" in result.stdout
    if analytics_status == "initialised":
        assert "analytics database is initialised" in result.stdout
        assert "initialise analytics:" not in result.stdout
    elif analytics_status == "uninitialised":
        assert "status is valid but the database is uninitialised" in result.stdout
        assert (
            f"initialise analytics: /usr/bin/python3 {analytics_program} "
            f"initialise --project-dir {runtime_project}"
            in result.stdout
        )
    elif analytics_status == "command_failure":
        assert "analytics readiness failure: status command failed" in result.stderr
    else:
        assert "analytics readiness failure: malformed status output" in result.stderr


def test_user_unit_installer_rejects_production_runtime_override(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("MRS_TEST_MODE", None)
    env["MRS_RUNTIME_PROJECT_DIR"] = str(tmp_path / "runtime-project")

    result = subprocess.run(
        [str(SYSTEMD_DIR / "install.sh"), "--check"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 2
    assert "MRS_RUNTIME_PROJECT_DIR is test-only" in result.stderr


def test_user_unit_installer_rejects_prospective_output_override_without_test_mode(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.pop("MRS_TEST_MODE", None)
    env["MRS_PROSPECTIVE_CONVERSATION_DIR"] = str(tmp_path / "output")

    result = subprocess.run(
        [str(SYSTEMD_DIR / "install.sh"), "--check"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 2
    assert "MRS_PROSPECTIVE_CONVERSATION_DIR is test-only" in result.stderr


def test_all_tracked_user_units_pass_systemd_verify() -> None:
    units = sorted(
        path
        for path in SYSTEMD_DIR.iterdir()
        if path.suffix in {".service", ".timer"}
    )
    result = subprocess.run(
        ["systemd-analyze", "--user", "verify", *(str(path) for path in units)],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_openai_collector_environment_example_contains_only_placeholders() -> None:
    example = (PROJECT_DIR / "mrsMThatcher.env.example").read_text(encoding="utf-8")
    assert "# Collector-only OpenAI organization administration settings." in example
    assert "OPENAI_ADMIN_API_KEY=\n" in example
    assert "OPENAI_COST_PROJECT_ID=\n" in example
    assert "proj_" not in example


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
