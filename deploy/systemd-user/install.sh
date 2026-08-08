#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "${SOURCE_DIR}/../.." && pwd -P)"
readonly TARGET_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
readonly SHADOW_HEALTH_DIR="${MRS_SEMANTIC_VETO_HEALTH_DIR:-/home/tonym/.local/state/mrsMThatcher/semantic-veto-health}"
readonly ANALYTICS_PROGRAM="${PROJECT_DIR}/mrs_engagement_analytics.py"
readonly UNITS=(
  mrsMThatcher.service
  mrs-engagement-analytics.service
  mrs-engagement-analytics.timer
  mrs-semantic-veto-shadow-health.service
  mrs-semantic-veto-shadow-health.timer
)

usage() {
  cat <<'EOF'
Usage: deploy/systemd-user/install.sh --check|--install

  --check    Validate tracked units and report drift from the user installation.
  --install  Copy tracked units, prepare private state, enable safe units, and reload systemd.

Installation never starts, stops, or restarts a unit. The analytics timer is
enabled only when its existing database passes the non-mutating status check.
EOF
}

verify_sources() {
  local paths=()
  local unit
  for unit in "${UNITS[@]}"; do
    paths+=("${SOURCE_DIR}/${unit}")
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
    elif ! cmp -s -- "${SOURCE_DIR}/${unit}" "${TARGET_DIR}/${unit}"; then
      printf 'drift: %s\n' "${TARGET_DIR}/${unit}" >&2
      failed=1
    else
      printf 'ok: %s\n' "${unit}"
    fi
  done
  return "${failed}"
}

analytics_is_initialised() {
  local status_json
  if ! status_json="$(/usr/bin/python3 "${ANALYTICS_PROGRAM}" status --project-dir "${PROJECT_DIR}")"; then
    return 1
  fi
  printf '%s' "${status_json}" | /usr/bin/python3 -c \
    'import json, sys; raise SystemExit(0 if json.load(sys.stdin).get("initialised") is True else 1)'
}

prepare_scheduled_task_state() {
  install -d -m 0700 -- "${SHADOW_HEALTH_DIR}" "${SHADOW_HEALTH_DIR}/history"
  printf 'prepared private semantic-veto health state: %s\n' "${SHADOW_HEALTH_DIR}"
}

install_units() {
  prepare_scheduled_task_state
  mkdir -p -- "${TARGET_DIR}"

  local unit temporary
  for unit in "${UNITS[@]}"; do
    temporary="$(mktemp "${TARGET_DIR}/.${unit}.XXXXXX")"
    install -m 0644 -- "${SOURCE_DIR}/${unit}" "${temporary}"
    mv -f -- "${temporary}" "${TARGET_DIR}/${unit}"
    printf 'installed: %s\n' "${TARGET_DIR}/${unit}"
  done

  systemctl --user daemon-reload
  systemctl --user enable mrsMThatcher.service
  systemctl --user enable mrs-semantic-veto-shadow-health.timer
  if analytics_is_initialised; then
    systemctl --user enable mrs-engagement-analytics.timer
    printf '%s\n' 'enabled analytics timer after non-mutating database status check'
  else
    systemctl --user disable mrs-engagement-analytics.timer
    printf '%s\n' 'analytics database is not initialised; analytics timer remains disabled'
    printf 'run: /usr/bin/python3 %q initialise --project-dir %q\n' \
      "${ANALYTICS_PROGRAM}" "${PROJECT_DIR}"
  fi
  printf '%s\n' 'user systemd manager reloaded; enabled units were not started or restarted'
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
