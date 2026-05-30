#!/usr/bin/env python3
"""Poll Modbus pose registers and write snapshots to InfluxDB."""

from __future__ import annotations

import sys

from app.services.influx_pose_writer import load_influx_pose_writer_config, run_pose_influx_writer_loop
from app.services.pose_modbus_common import wait_for_modbus_tcp


def main() -> int:
    cfg = load_influx_pose_writer_config()
    if not (cfg.influx_url and cfg.influx_org and cfg.influx_bucket and cfg.influx_token):
        print("[INFLUX-WRITER] Influx is not configured, exiting.", file=sys.stderr)
        return 2

    if not wait_for_modbus_tcp(cfg.modbus.host, cfg.modbus.port, timeout_s=120.0):
        print(
            f"[INFLUX-WRITER] Modbus server not ready at {cfg.modbus.host}:{cfg.modbus.port}",
            file=sys.stderr,
        )
        return 3

    run_pose_influx_writer_loop(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
