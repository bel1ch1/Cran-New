#!/usr/bin/env bash
set -euo pipefail

# This script is designed to be called by Linux watchdog daemon via
# "test-binary". It returns:
# - 0 when Docker stack health is acceptable;
# - non-zero when the stack is unhealthy for too long.

STATE_DIR="${CRAN_WATCHDOG_STATE_DIR:-/run/cran-watchdog}"
STATE_FILE="${STATE_DIR}/fail_count"
MAX_FAILS="${CRAN_WATCHDOG_MAX_FAILS:-3}"

# Space-separated list of expected container names.
SERVICES_RAW="${CRAN_WATCHDOG_SERVICES:-cran_calibration_app cran_bridge_supervisor cran_hook_supervisor}"

mkdir -p "${STATE_DIR}"

read_fail_count() {
  if [[ -f "${STATE_FILE}" ]]; then
    cat "${STATE_FILE}"
  else
    echo "0"
  fi
}

write_fail_count() {
  printf "%s" "$1" > "${STATE_FILE}"
}

is_service_healthy() {
  local service="$1"
  local status
  local health

  status="$(docker inspect --format '{{.State.Status}}' "${service}" 2>/dev/null || true)"
  if [[ "${status}" != "running" ]]; then
    return 1
  fi

  # If healthcheck exists, require healthy state.
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${service}" 2>/dev/null || true)"
  if [[ "${health}" == "none" || "${health}" == "healthy" ]]; then
    return 0
  fi
  return 1
}

main() {
  local current_fail_count
  local next_fail_count
  local service

  current_fail_count="$(read_fail_count)"
  next_fail_count=0

  for service in ${SERVICES_RAW}; do
    if ! is_service_healthy "${service}"; then
      next_fail_count=$((current_fail_count + 1))
      write_fail_count "${next_fail_count}"
      echo "watchdog-check: unhealthy service=${service}, fail_count=${next_fail_count}/${MAX_FAILS}" >&2
      if (( next_fail_count >= MAX_FAILS )); then
        # Non-zero exit prevents watchdog from petting /dev/watchdog.
        return 1
      fi
      return 0
    fi
  done

  # Everything is healthy: reset failure counter.
  write_fail_count "0"
  return 0
}

main "$@"
