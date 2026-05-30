#!/usr/bin/env bash
set -euo pipefail

# Called by Linux watchdog daemon via test-binary.
# Returns 0 to keep feeding /dev/watchdog, non-zero to trigger hardware reboot
# after watchdog-timeout seconds.

STATE_DIR="${CRAN_WATCHDOG_STATE_DIR:-/run/cran-watchdog}"
STATE_FILE="${STATE_DIR}/fail_count"
FIRST_HEALTHY_FILE="${STATE_DIR}/first_healthy"
LOG_TAG="cran-watchdog"

# Check interval is configured in /etc/watchdog.conf (interval = ...).
CHECK_INTERVAL_S="${CRAN_WATCHDOG_CHECK_INTERVAL_S:-300}"
BOOT_GRACE_S="${CRAN_WATCHDOG_BOOT_GRACE_S:-900}"
STARTUP_TIMEOUT_S="${CRAN_WATCHDOG_STARTUP_TIMEOUT_S:-1800}"
MAX_FAILS="${CRAN_WATCHDOG_MAX_FAILS:-3}"
DATA_DIR="${CRAN_WATCHDOG_DATA_DIR:-/home/cran/Cran-New/data/runtime}"

SERVICES_RAW="${CRAN_WATCHDOG_SERVICES:-cran_calibration_app cran_bridge_supervisor cran_hook_supervisor cran_influxdb cran_pose_influx_writer}"

SUP_HEARTBEAT_MAX_AGE="${CRAN_WATCHDOG_SUP_HEARTBEAT_MAX_AGE:-120}"
CHILD_HEARTBEAT_MAX_AGE="${CRAN_WATCHDOG_CHILD_HEARTBEAT_MAX_AGE:-120}"
INFLUX_WRITER_HEARTBEAT_MAX_AGE="${CRAN_WATCHDOG_INFLUX_WRITER_HEARTBEAT_MAX_AGE:-300}"
LOCK_FILE="${CRAN_WATCHDOG_LOCK_FILE:-${DATA_DIR}/calibration.lock}"

mkdir -p "${STATE_DIR}"

log() {
  echo "${LOG_TAG}: $*" >&2
  logger -t "${LOG_TAG}" "$*" 2>/dev/null || true
}

uptime_s() {
  awk '{print int($1)}' /proc/uptime
}

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

reset_fail_count() {
  write_fail_count "0"
}

mark_first_healthy() {
  date +%s > "${FIRST_HEALTHY_FILE}"
}

has_been_healthy() {
  [[ -f "${FIRST_HEALTHY_FILE}" ]]
}

heartbeat_fresh() {
  local file="$1"
  local max_age="$2"
  local now="$3"

  [[ -s "${file}" ]] || return 1
  local ts
  ts="$(cat "${file}" 2>/dev/null || echo 0)"
  (( now - ts < max_age ))
}

docker_ready() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1
}

is_service_healthy() {
  local service="$1"
  local status
  local health

  status="$(docker inspect --format '{{.State.Status}}' "${service}" 2>/dev/null || true)"
  if [[ "${status}" != "running" ]]; then
    return 1
  fi

  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${service}" 2>/dev/null || true)"
  if [[ "${health}" == "none" || "${health}" == "healthy" ]]; then
    return 0
  fi
  return 1
}

check_host_heartbeats() {
  local now="$1"

  heartbeat_fresh "${DATA_DIR}/bridge_pose_supervisor.heartbeat" "${SUP_HEARTBEAT_MAX_AGE}" "${now}" || return 1
  heartbeat_fresh "${DATA_DIR}/hook_pose_supervisor.heartbeat" "${SUP_HEARTBEAT_MAX_AGE}" "${now}" || return 1

  if [[ ! -f "${LOCK_FILE}" ]]; then
    heartbeat_fresh "${DATA_DIR}/bridge_pose_modbus.heartbeat" "${CHILD_HEARTBEAT_MAX_AGE}" "${now}" || return 1
    heartbeat_fresh "${DATA_DIR}/hook_pose_modbus.heartbeat" "${CHILD_HEARTBEAT_MAX_AGE}" "${now}" || return 1
    heartbeat_fresh "${DATA_DIR}/pose_influx_writer.heartbeat" "${INFLUX_WRITER_HEARTBEAT_MAX_AGE}" "${now}" || return 1
  fi

  return 0
}

record_failure() {
  local reason="$1"
  local current
  local next

  current="$(read_fail_count)"
  next=$((current + 1))
  write_fail_count "${next}"
  log "FAIL (${next}/${MAX_FAILS}): ${reason}"

  if (( next >= MAX_FAILS )); then
    log "Failure threshold reached; allowing hardware watchdog reboot"
    return 1
  fi
  return 0
}

main() {
  local now
  local uptime
  local service

  now="$(date +%s)"
  uptime="$(uptime_s)"

  # Boot grace: never fail while OS/Docker stack is still starting.
  if (( uptime < BOOT_GRACE_S )); then
    log "boot grace active (${uptime}/${BOOT_GRACE_S}s uptime); skipping strict checks"
    reset_fail_count
    return 0
  fi

  if ! docker_ready; then
    if has_been_healthy; then
      record_failure "docker daemon is not ready"
      return $?
    fi
    if (( uptime < STARTUP_TIMEOUT_S )); then
      log "docker not ready during startup window (${uptime}/${STARTUP_TIMEOUT_S}s); waiting"
      reset_fail_count
      return 0
    fi
    record_failure "docker daemon not ready after startup timeout"
    return $?
  fi

  for service in ${SERVICES_RAW}; do
    if ! is_service_healthy "${service}"; then
      if has_been_healthy; then
        record_failure "container unhealthy: ${service}"
        return $?
      fi
      if (( uptime < STARTUP_TIMEOUT_S )); then
        log "container not healthy yet (${service}); startup window (${uptime}/${STARTUP_TIMEOUT_S}s)"
        reset_fail_count
        return 0
      fi
      record_failure "container not healthy after startup timeout: ${service}"
      return $?
    fi
  done

  if ! check_host_heartbeats "${now}"; then
    if has_been_healthy; then
      record_failure "stale heartbeat files under ${DATA_DIR}"
      return $?
    fi
    if (( uptime < STARTUP_TIMEOUT_S )); then
      log "heartbeats not fresh yet; startup window (${uptime}/${STARTUP_TIMEOUT_S}s)"
      reset_fail_count
      return 0
    fi
    record_failure "stale heartbeat files after startup timeout"
    return $?
  fi

  if ! has_been_healthy; then
    mark_first_healthy
    log "stack became healthy for the first time; strict monitoring enabled"
  fi

  reset_fail_count
  log "OK (interval=${CHECK_INTERVAL_S}s, uptime=${uptime}s)"
  return 0
}

main "$@"
