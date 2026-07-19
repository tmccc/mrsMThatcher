#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TARGET_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
readonly UNITS=(
  mrsMThatcher.service
  mrs-engagement-analytics.service
  mrs-engagement-analytics.timer
)

usage() {
  cat <<'EOF'
Usage: deploy/systemd-user/install.sh --check|--install

  --check    Validate tracked units and report drift from the user installation.
  --install  Atomically copy tracked units and reload the user systemd manager.

Installation does not enable, start, stop, or restart any unit.
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

install_units() {
  mkdir -p -- "${TARGET_DIR}"

  local unit temporary
  for unit in "${UNITS[@]}"; do
    temporary="$(mktemp "${TARGET_DIR}/.${unit}.XXXXXX")"
    install -m 0644 -- "${SOURCE_DIR}/${unit}" "${temporary}"
    mv -f -- "${temporary}" "${TARGET_DIR}/${unit}"
    printf 'installed: %s\n' "${TARGET_DIR}/${unit}"
  done

  systemctl --user daemon-reload
  printf '%s\n' 'user systemd manager reloaded; no unit was restarted or enabled'
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
