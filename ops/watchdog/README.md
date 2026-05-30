# Hardware Watchdog Setup (Raspberry Pi / Linux)

Hardware watchdog reboots the host when the CRAN Docker stack stays unhealthy for too long.

## Safety against boot loops

The guard script **never triggers reboot** during:

1. **Boot grace** (`CRAN_WATCHDOG_BOOT_GRACE_S`, default **900 s / 15 min**) — OS and Docker are still starting.
2. **Startup window** (`CRAN_WATCHDOG_STARTUP_TIMEOUT_S`, default **1800 s / 30 min**) — if the stack has **never** been healthy yet, checks keep passing.
3. **First healthy marker** — strict failure counting starts only after the stack was healthy at least once.

Additionally:

- `watchdog.service` starts **after** `docker.service` (+ 30 s delay).
- Failed checks require **3 consecutive** intervals (default 5 min) => ~15 min before reboot.

## Install

```bash
cd /home/cran/Cran-New
sudo chmod +x ops/watchdog/install.sh
sudo ops/watchdog/install.sh
```

This installs:

- `/opt/cran/watchdog/docker_health_guard.sh`
- `/etc/cran/watchdog.env` (from example, if missing)
- `/etc/watchdog.conf` (interval/timeout from env)
- `/etc/systemd/system/watchdog.service.d/cran.conf`

## Configuration

Edit `/etc/cran/watchdog.env`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `CRAN_WATCHDOG_CHECK_INTERVAL_S` | `300` | Check every 5 minutes |
| `CRAN_WATCHDOG_BOOT_GRACE_S` | `900` | No failures during first 15 min after boot |
| `CRAN_WATCHDOG_STARTUP_TIMEOUT_S` | `1800` | Wait up to 30 min for first healthy stack |
| `CRAN_WATCHDOG_MAX_FAILS` | `3` | Failed checks before reboot |
| `CRAN_WATCHDOG_DATA_DIR` | project `data/runtime` | Heartbeat files on host |
| `CRAN_WATCHDOG_TIMEOUT_S` | `interval * 3` | Hardware watchdog timeout in `/etc/watchdog.conf` |

After changes:

```bash
sudo ops/watchdog/install.sh
```

## What is checked

Docker containers (must be `running` + `healthy` when healthcheck exists):

- `cran_calibration_app`
- `cran_bridge_supervisor`
- `cran_hook_supervisor`
- `cran_influxdb`
- `cran_pose_influx_writer`

Host heartbeat files under `data/runtime/`:

- `*_pose_supervisor.heartbeat`
- `*_pose_modbus.heartbeat` (skipped while `calibration.lock` exists)
- `pose_influx_writer.heartbeat`

## Docker healthchecks

Container healthchecks use the same heartbeat files via:

- `scripts/healthcheck_pose_supervisor.sh`
- `scripts/healthcheck_influx_writer.sh`

## Logs

```bash
journalctl -t cran-watchdog -f
sudo systemctl status watchdog
```

## Uninstall / disable

```bash
sudo systemctl disable --now watchdog
sudo rm -f /etc/systemd/system/watchdog.service.d/cran.conf
sudo systemctl daemon-reload
```
