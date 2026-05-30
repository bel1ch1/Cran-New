#!/bin/sh
set -eu

HB="${CRAN_INFLUX_WRITER_HEARTBEAT_FILE:-/app/data/runtime/pose_influx_writer.heartbeat}"
MAX_AGE="${CRAN_HEALTHCHECK_INFLUX_WRITER_HEARTBEAT_MAX_AGE:-120}"
NOW="$(date +%s)"

[ -s "${HB}" ] || exit 1
age=$((NOW - $(cat "${HB}" 2>/dev/null || echo 0)))
[ "${age}" -lt "${MAX_AGE}" ]
