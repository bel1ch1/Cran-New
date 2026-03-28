# Hardware Watchdog Setup (Raspberry Pi / Linux)

This directory contains files to enable host reboot on persistent docker stack failures.

## Files

- `docker_health_guard.sh` - test script for watchdog daemon.
- `watchdog.conf` - example watchdog daemon config.

## Install

1. Install watchdog package:

```bash
sudo apt-get update
sudo apt-get install -y watchdog
```

2. Ensure hardware watchdog module is available:

```bash
ls -l /dev/watchdog
```

3. Copy script and make it executable:

```bash
sudo mkdir -p /opt/cran/watchdog
sudo cp ops/watchdog/docker_health_guard.sh /opt/cran/watchdog/docker_health_guard.sh
sudo chmod +x /opt/cran/watchdog/docker_health_guard.sh
```

4. Apply watchdog config:

```bash
sudo cp /etc/watchdog.conf /etc/watchdog.conf.bak
sudo cp ops/watchdog/watchdog.conf /etc/watchdog.conf
```

5. Start and enable watchdog:

```bash
sudo systemctl enable watchdog
sudo systemctl restart watchdog
sudo systemctl status watchdog
```

## Behavior

- `docker_health_guard.sh` checks:
  - `cran_calibration_app`
  - `cran_bridge_supervisor`
  - `cran_hook_supervisor`
- If any service is not `running` or `healthy`, failure counter increases.
- After `CRAN_WATCHDOG_MAX_FAILS` consecutive failures (default: `3`), script returns non-zero.
- Watchdog daemon then stops feeding `/dev/watchdog`, and hardware reboots the host after timeout.

## Tuning

Environment variables (set in watchdog service environment if needed):

- `CRAN_WATCHDOG_MAX_FAILS` (default `3`)
- `CRAN_WATCHDOG_SERVICES` (space-separated container names)
- `CRAN_WATCHDOG_STATE_DIR` (default `/run/cran-watchdog`)
