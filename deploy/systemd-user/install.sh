#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly UNIT_SOURCE_DIR="${SOURCE_PROJECT_DIR}/deploy/systemd-user"
readonly INHERITED_TEST_MODE="${MRS_TEST_MODE:-}"
if [[ -n "${MRS_RUNTIME_PROJECT_DIR:-}" && "${INHERITED_TEST_MODE}" != 1 ]]; then
  printf '%s\n' 'MRS_RUNTIME_PROJECT_DIR is test-only and is refused unless MRS_TEST_MODE=1 is inherited' >&2
  exit 2
fi
if [[ -n "${MRS_PROSPECTIVE_CONVERSATION_DIR:-}" && "${INHERITED_TEST_MODE}" != 1 ]]; then
  printf '%s\n' 'MRS_PROSPECTIVE_CONVERSATION_DIR is test-only and is refused unless MRS_TEST_MODE=1 is inherited' >&2
  exit 2
fi
readonly RUNTIME_PROJECT_DIR="${MRS_RUNTIME_PROJECT_DIR:-/disks/disk1/etc/mrsMThatcher}"
readonly TARGET_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
readonly SHADOW_HEALTH_DIR="${HOME}/.local/state/mrsMThatcher/semantic-veto-health"
readonly OPENAI_COST_DIR="${HOME}/.local/state/mrsMThatcher/openai-costs"
readonly PROSPECTIVE_CONVERSATION_DIR="${MRS_PROSPECTIVE_CONVERSATION_DIR:-/disks/disk1/research/mrsMThatcher-prospective-conversations}"
readonly ANALYTICS_PROGRAM="${RUNTIME_PROJECT_DIR}/mrs_engagement_analytics.py"
readonly UNITS=(
  mrsMThatcher.service
  mrs-engagement-analytics.service
  mrs-engagement-analytics.timer
  mrs-openai-cost-cache.service
  mrs-openai-cost-cache.timer
  mrs-prospective-conversations.service
  mrs-prospective-conversations.timer
  mrs-semantic-veto-shadow-health.service
  mrs-semantic-veto-shadow-health.timer
)

usage() {
  cat <<'EOF'
Usage: deploy/systemd-user/install.sh --check|--install

  --check    Validate tracked units and report drift from the user installation.
  --install  Copy tracked units, prepare private state, and reload systemd.

Installation does not enable, disable, start, stop, or restart any unit. It
reports analytics readiness and prints separate operator activation commands.
EOF
}

verify_sources() {
  local paths=()
  local unit
  for unit in "${UNITS[@]}"; do
    paths+=("${UNIT_SOURCE_DIR}/${unit}")
  done
  systemd-analyze --user verify "${paths[@]}"
}

check_installation() {
  local failed=0
  local unit
  for unit in "${UNITS[@]}"; do
    if [[ ! -f "${TARGET_DIR}/${unit}" ]]; then
      printf 'missing: %s\n' "${TARGET_DIR}/${unit}" >&2
      failed=1
    elif ! cmp -s -- "${UNIT_SOURCE_DIR}/${unit}" "${TARGET_DIR}/${unit}"; then
      printf 'drift: %s\n' "${TARGET_DIR}/${unit}" >&2
      failed=1
    else
      printf 'ok: %s\n' "${unit}"
    fi
  done
  return "${failed}"
}

report_analytics_readiness() {
  local readiness status_json
  if ! status_json="$(/usr/bin/python3 "${ANALYTICS_PROGRAM}" status --project-dir "${RUNTIME_PROJECT_DIR}")"; then
    printf 'analytics readiness failure: status command failed for runtime directory %s\n' \
      "${RUNTIME_PROJECT_DIR}" >&2
    return 1
  fi
  if ! readiness="$(printf '%s' "${status_json}" | /usr/bin/python3 -c \
    'import json, sys
data = json.load(sys.stdin)
if not isinstance(data, dict) or type(data.get("initialised")) is not bool:
    raise ValueError("invalid analytics status")
print("initialised" if data["initialised"] else "uninitialised")' \
    2>/dev/null)"; then
    printf 'analytics readiness failure: malformed status output for runtime directory %s\n' \
      "${RUNTIME_PROJECT_DIR}" >&2
    return 1
  fi
  if [[ "${readiness}" == initialised ]]; then
    printf 'analytics database is initialised: %s\n' "${RUNTIME_PROJECT_DIR}"
    return 0
  fi
  printf 'analytics status is valid but the database is uninitialised: %s\n' \
    "${RUNTIME_PROJECT_DIR}"
  printf 'initialise analytics: /usr/bin/python3 %q initialise --project-dir %q\n' \
    "${ANALYTICS_PROGRAM}" "${RUNTIME_PROJECT_DIR}"
}

print_enable_commands() {
  printf '%s\n' \
    'enable each desired unit separately:' \
    '  systemctl --user enable mrsMThatcher.service' \
    '  systemctl --user enable mrs-semantic-veto-shadow-health.timer' \
    '  systemctl --user enable mrs-engagement-analytics.timer' \
    '  systemctl --user enable --now mrs-openai-cost-cache.timer' \
    '  systemctl --user enable --now mrs-prospective-conversations.timer' \
    'suggested prospective-conversation first run:' \
    '  systemctl --user start mrs-prospective-conversations.service'
}

prepare_scheduled_task_state() {
  install -d -m 0700 -- \
    "${SHADOW_HEALTH_DIR}" \
    "${SHADOW_HEALTH_DIR}/history" \
    "${OPENAI_COST_DIR}" \
    "${PROSPECTIVE_CONVERSATION_DIR}" \
    "${PROSPECTIVE_CONVERSATION_DIR}/state" \
    "${PROSPECTIVE_CONVERSATION_DIR}/batches" \
    "${PROSPECTIVE_CONVERSATION_DIR}/review-packs"
  printf 'prepared private semantic-veto health state: %s\n' "${SHADOW_HEALTH_DIR}"
  printf 'prepared private OpenAI cost state: %s\n' "${OPENAI_COST_DIR}"
  printf 'prepared private prospective conversation state: %s\n' \
    "${PROSPECTIVE_CONVERSATION_DIR}"
}

install_units() {
  prepare_scheduled_task_state
  mkdir -p -- "${TARGET_DIR}"

  local unit temporary
  for unit in "${UNITS[@]}"; do
    temporary="$(mktemp "${TARGET_DIR}/.${unit}.XXXXXX")"
    install -m 0644 -- "${UNIT_SOURCE_DIR}/${unit}" "${temporary}"
    mv -f -- "${temporary}" "${TARGET_DIR}/${unit}"
    printf 'installed: %s\n' "${TARGET_DIR}/${unit}"
  done

  systemctl --user daemon-reload
  local readiness_rc=0
  report_analytics_readiness || readiness_rc=$?
  print_enable_commands
  printf '%s\n' 'user systemd manager reloaded; no unit activation state was changed'
  return "${readiness_rc}"
}

main() {
  if [[ $# -ne 1 ]]; then
    usage >&2
    return 2
  fi

  verify_sources
  case "$1" in
    --check)
      check_installation
      ;;
    --install)
      install_units
      ;;
    --help|-h)
      usage
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

main "$@"
