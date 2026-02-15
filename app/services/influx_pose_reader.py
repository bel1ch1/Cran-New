from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, parse, request


@dataclass
class InfluxPoseConfig:
    url: str
    org: str
    bucket: str
    token: str
    measurement: str
    field_bridge_x: str
    field_bridge_y: str
    field_hook_distance: str


def _to_iso_millis(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return ts
    return dt.isoformat(timespec="milliseconds")


def _parse_influx_csv(body_text: str, accepted_fields: set[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {field: [] for field in accepted_fields}
    reader = csv.DictReader(io.StringIO(body_text))
    for row in reader:
        field_name = row.get("_field")
        if field_name not in accepted_fields:
            continue
        value_raw = row.get("_value")
        ts_raw = row.get("_time")
        if value_raw is None or ts_raw is None:
            continue
        try:
            value = float(value_raw)
        except ValueError:
            continue
        result[field_name].append({"t": _to_iso_millis(ts_raw), "v": value})
    return result


def read_pose_history_from_influx(
    cfg: InfluxPoseConfig,
    minutes: int = 60,
    max_points: int = 300,
) -> dict[str, Any]:
    if not (cfg.url and cfg.org and cfg.bucket and cfg.token):
        return {
            "enabled": False,
            "connected": False,
            "source": "live_fallback",
            "error": "Influx is not configured",
            "series": {},
        }

    fields = [cfg.field_bridge_x, cfg.field_bridge_y, cfg.field_hook_distance]
    accepted_fields = set(fields)
    minutes = max(1, int(minutes))
    max_points = max(20, min(2000, int(max_points)))
    every_seconds = max(1, int((minutes * 60) / max_points))
    every = f"{every_seconds}s"
    quoted_fields = " or ".join([f'r._field == "{field}"' for field in fields])
    flux = (
        f'from(bucket: "{cfg.bucket}")'
        f" |> range(start: -{minutes}m)"
        f' |> filter(fn: (r) => r._measurement == "{cfg.measurement}")'
        f" |> filter(fn: (r) => {quoted_fields})"
        f" |> aggregateWindow(every: {every}, fn: last, createEmpty: false)"
        " |> keep(columns: [\"_time\", \"_field\", \"_value\"])"
    )

    endpoint = cfg.url.rstrip("/") + "/api/v2/query?" + parse.urlencode({"org": cfg.org})
    payload = json.dumps({"query": flux, "type": "flux"}).encode("utf-8")
    headers = {
        "Authorization": f"Token {cfg.token}",
        "Content-Type": "application/json",
        "Accept": "text/csv",
    }
    req = request.Request(endpoint, data=payload, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=4.0) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        return {
            "enabled": True,
            "connected": False,
            "source": "live_fallback",
            "error": f"Influx request failed: {exc}",
            "series": {},
        }

    series = _parse_influx_csv(text, accepted_fields=accepted_fields)
    return {
        "enabled": True,
        "connected": True,
        "source": "influxdb",
        "error": None,
        "series": {
            "bridge_x_m": series.get(cfg.field_bridge_x, []),
            "bridge_y_m": series.get(cfg.field_bridge_y, []),
            "hook_distance_m": series.get(cfg.field_hook_distance, []),
        },
    }
