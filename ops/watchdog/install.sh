#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_SRC="${ROOT_DIR}/ops/watchdog/cran-watchdog.env.example"
ENV_DST="/etc/cran/watchdog.env"
SCRIPT_DST="/opt/cran/watchdog/docker_health_guard.sh"
WATCHDOG_CONF="/etc/watchdog.conf"
DROPIN_DIR="/etc/systemd/system/watchdog.service.d"
DROPIN_DST="${DROPIN_DIR}/cran.conf"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo ${ROOT_DIR}/ops/watchdog/install.sh" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "apt-get not found; install watchdog package manually." >&2
  exit 1
fi

apt-get update
apt-get install -y watchdog

mkdir -p /opt/cran/watchdog /etc/cran "${DROPIN_DIR}"
install -m 0755 "${ROOT_DIR}/ops/watchdog/docker_health_guard.sh" "${SCRIPT_DST}"

if [[ ! -f "${ENV_DST}" ]]; then
  install -m 0644 "${ENV_SRC}" "${ENV_DST}"
  echo "Created ${ENV_DST} from example."
else
  echo "Keeping existing ${ENV_DST}."
fi

# shellcheck disable=SC1090
source "${ENV_DST}"

INTERVAL="${CRAN_WATCHDOG_CHECK_INTERVAL_S:-300}"
TIMEOUT="${CRAN_WATCHDOG_TIMEOUT_S:-$((INTERVAL * 3))}"
if (( TIMEOUT <= INTERVAL )); then
  TIMEOUT=$((INTERVAL * 3))
fi

sed \
  -e "s/__WATCHDOG_INTERVAL__/${INTERVAL}/" \
  -e "s/__WATCHDOG_TIMEOUT__/${TIMEOUT}/" \
  "${ROOT_DIR}/ops/watchdog/watchdog.conf.template" > "${WATCHDOG_CONF}"

install -m 0644 "${ROOT_DIR}/ops/watchdog/watchdog.service.d-cran.conf" "${DROPIN_DST}"

systemctl daemon-reload
systemctl enable watchdog
systemctl restart watchdog

echo
echo "CRAN watchdog installed."
echo "  interval: ${INTERVAL}s"
echo "  watchdog-timeout: ${TIMEOUT}s"
echo "  env file: ${ENV_DST}"
echo
echo "Edit ${ENV_DST} and re-run:"
echo "  sudo ${ROOT_DIR}/ops/watchdog/install.sh"
echo
systemctl --no-pager --full status watchdog || true
