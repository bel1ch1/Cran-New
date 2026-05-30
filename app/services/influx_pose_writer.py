from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from app.services.modbus_pose_reader import ModbusPoseReaderConfig, read_pose_values


@dataclass
class InfluxPoseWriterConfig:
    influx_url: str
    influx_org: str
    influx_bucket: str
    influx_token: str
    influx_measurement: str
    field_bridge_x: str
    field_bridge_y: str
    field_hook_distance: str
    modbus: ModbusPoseReaderConfig
    interval_s: float = 1.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def load_influx_pose_writer_config() -> InfluxPoseWriterConfig:
    return InfluxPoseWriterConfig(
        influx_url=os.getenv("CRAN_INFLUX_URL", "").strip(),
        influx_org=os.getenv("CRAN_INFLUX_ORG", "").strip(),
        influx_bucket=os.getenv("CRAN_INFLUX_BUCKET", "").strip(),
        influx_token=os.getenv("CRAN_INFLUX_TOKEN", "").strip(),
        influx_measurement=os.getenv("CRAN_INFLUX_MEASUREMENT", "crane_pose"),
        field_bridge_x=os.getenv("CRAN_INFLUX_FIELD_BRIDGE_X", "bridge_x_m"),
        field_bridge_y=os.getenv("CRAN_INFLUX_FIELD_BRIDGE_Y", "bridge_y_m"),
        field_hook_distance=os.getenv("CRAN_INFLUX_FIELD_HOOK_DISTANCE", "hook_distance_m"),
        modbus=ModbusPoseReaderConfig(
            host=os.getenv("CRAN_MODBUS_HOST", "127.0.0.1"),
            port=int(os.getenv("CRAN_MODBUS_PORT", "5020")),
            unit_id=int(os.getenv("CRAN_MODBUS_UNIT_ID", "1")),
            bridge_base_register=int(os.getenv("CRAN_MODBUS_BRIDGE_BASE_REGISTER", "100")),
            hook_base_register=int(os.getenv("CRAN_MODBUS_HOOK_BASE_REGISTER", "200")),
        ),
        interval_s=max(0.2, _env_float("CRAN_INFLUX_WRITE_INTERVAL", 1.0)),
    )


def _write_line_protocol(cfg: InfluxPoseWriterConfig, line: str) -> None:
    endpoint = (
        cfg.influx_url.rstrip("/")
        + "/api/v2/write?"
        + parse.urlencode(
            {
                "org": cfg.influx_org,
                "bucket": cfg.influx_bucket,
                "precision": "s",
            }
        )
    )
    req = request.Request(
        endpoint,
        data=line.encode("utf-8"),
        headers={
            "Authorization": f"Token {cfg.influx_token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=4.0) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Influx write failed with HTTP {resp.status}")


def build_pose_line(cfg: InfluxPoseWriterConfig, pose: dict[str, Any]) -> str | None:
    bridge = pose.get("bridge") or {}
    hook = pose.get("hook") or {}
    fields = [
        f"{cfg.field_bridge_x}={float(bridge.get('x_m', 0.0))}",
        f"{cfg.field_bridge_y}={float(bridge.get('y_m', 0.0))}",
        f"{cfg.field_hook_distance}={float(hook.get('distance_m', 0.0))}",
        f"bridge_valid={1 if bridge.get('valid') else 0}i",
        f"hook_valid={1 if hook.get('valid') else 0}i",
    ]
    timestamp = int(time.time())
    return f"{cfg.influx_measurement} {','.join(fields)} {timestamp}"


def write_pose_snapshot(cfg: InfluxPoseWriterConfig) -> dict[str, Any]:
    if not (cfg.influx_url and cfg.influx_org and cfg.influx_bucket and cfg.influx_token):
        return {"ok": False, "error": "Influx is not configured"}

    pose = read_pose_values(cfg.modbus)
    if not pose.get("connected"):
        return {"ok": False, "error": pose.get("error") or "Modbus is not connected", "pose": pose}

    line = build_pose_line(cfg, pose)
    if line is None:
        return {"ok": False, "error": "No pose line generated", "pose": pose}

    try:
        _write_line_protocol(cfg, line)
    except error.URLError as exc:
        return {"ok": False, "error": f"Influx write failed: {exc}", "pose": pose}

    return {"ok": True, "error": None, "pose": pose}


def run_pose_influx_writer_loop(cfg: InfluxPoseWriterConfig) -> None:
    heartbeat_path = Path(os.getenv("CRAN_INFLUX_WRITER_HEARTBEAT_FILE", "data/runtime/pose_influx_writer.heartbeat"))
    print(
        "[INFLUX-WRITER] Starting. "
        f"modbus={cfg.modbus.host}:{cfg.modbus.port}, "
        f"influx={cfg.influx_url}, bucket={cfg.influx_bucket}, "
        f"interval={cfg.interval_s}s"
    )
    while True:
        result = write_pose_snapshot(cfg)
        if result["ok"]:
            pose = result.get("pose") or {}
            bridge = pose.get("bridge") or {}
            hook = pose.get("hook") or {}
            try:
                heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                heartbeat_path.write_text(str(int(time.time())), encoding="utf-8")
            except Exception:
                pass
            print(
                "[INFLUX-WRITER] wrote "
                f"x={bridge.get('x_m')} y={bridge.get('y_m')} "
                f"hook={hook.get('distance_m')}"
            )
        else:
            print(f"[INFLUX-WRITER] skip: {result.get('error')}")
        time.sleep(cfg.interval_s)
