#!/bin/sh
set -eu

ROLE="${1:?role required: bridge or hook}"
BASE="/app/data/runtime"
SUP_PID="${BASE}/${ROLE}_pose_supervisor.pid"
CHILD_PID="${BASE}/${ROLE}_pose_modbus.pid"
SUP_HB="${BASE}/${ROLE}_pose_supervisor.heartbeat"
CHILD_HB="${BASE}/${ROLE}_pose_modbus.heartbeat"
LOCK="${CRAN_SUPERVISOR_LOCK_FILE:-/app/data/runtime/calibration.lock}"
MAX_SUP_AGE="${CRAN_HEALTHCHECK_SUP_HEARTBEAT_MAX_AGE:-30}"
MAX_CHILD_AGE="${CRAN_HEALTHCHECK_CHILD_HEARTBEAT_MAX_AGE:-45}"
NOW="$(date +%s)"

heartbeat_fresh() {
  file="$1"
  max_age="$2"
  [ -s "${file}" ] || return 1
  age=$((NOW - $(cat "${file}" 2>/dev/null || echo 0)))
  [ "${age}" -lt "${max_age}" ]
}

[ -s "${SUP_PID}" ] || exit 1
[ -s "${CHILD_PID}" ] || exit 1
heartbeat_fresh "${SUP_HB}" "${MAX_SUP_AGE}" || exit 1

if [ ! -f "${LOCK}" ]; then
  heartbeat_fresh "${CHILD_HB}" "${MAX_CHILD_AGE}" || exit 1
fi

exit 0
