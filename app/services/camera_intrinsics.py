from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_CAMERA_INTRINSICS: dict[str, dict[str, list[list[float]] | list[float]]] = {
    "camera_0": {
        "camera_matrix": [
            [1759.5451643196482,0,759.0844499851064],
            [0, 1749.616531023043, 235.46847233900277],
            [0, 0, 1],
        ],
        "dist_coeffs": [
            0.06668622890462314,
            0.6740369333070254,
            -0.02239890744409157,
            0.008551901066880528,
            -3.1282836536539893
        ],
    },
    "camera_1": {
        "camera_matrix": [
            [1751.7585483009766,0,736.5449420545249],
            [0,1742.482910862439,248.95145390942164],
            [0,0,1],
        ],
        "dist_coeffs": [
            0.07964752643577983,
            0.5411208927538892,
            -0.01842137076906162,
            0.009431718517098085,
            -3.4709927899809325
        ],
    },
}


def get_default_camera_intrinsics_payload() -> dict[str, Any]:
    return deepcopy(DEFAULT_CAMERA_INTRINSICS)


def _to_camera_matrix(raw_value: Any, fallback_value: Any) -> np.ndarray:
    try:
        matrix = np.array(raw_value, dtype=np.float32)
        if matrix.shape != (3, 3):
            raise ValueError("camera_matrix must be 3x3")
        return matrix
    except Exception:
        return np.array(fallback_value, dtype=np.float32)


def _to_dist_coeffs(raw_value: Any, fallback_value: Any) -> np.ndarray:
    try:
        coeffs = np.array(raw_value, dtype=np.float32).reshape(-1)
        if coeffs.size < 5:
            raise ValueError("dist_coeffs must contain at least 5 values")
        return coeffs.reshape(1, -1)
    except Exception:
        fallback = np.array(fallback_value, dtype=np.float32).reshape(-1)
        return fallback.reshape(1, -1)


def normalize_camera_intrinsics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = get_default_camera_intrinsics_payload()
    if not isinstance(payload, dict):
        return normalized

    for camera_key, defaults in DEFAULT_CAMERA_INTRINSICS.items():
        raw_camera = payload.get(camera_key)
        if not isinstance(raw_camera, dict):
            continue
        camera_matrix = raw_camera.get("camera_matrix")
        if isinstance(camera_matrix, list):
            normalized[camera_key]["camera_matrix"] = camera_matrix
        dist_coeffs = raw_camera.get("dist_coeffs")
        if isinstance(dist_coeffs, list):
            normalized[camera_key]["dist_coeffs"] = dist_coeffs
    return normalized


def intrinsics_from_payload(payload: dict[str, Any], camera_id: int) -> tuple[np.ndarray, np.ndarray]:
    camera_key = f"camera_{int(camera_id)}"
    defaults = DEFAULT_CAMERA_INTRINSICS.get(camera_key) or DEFAULT_CAMERA_INTRINSICS["camera_0"]

    camera_intrinsics = payload.get("camera_intrinsics", {}) if isinstance(payload, dict) else {}
    selected_raw = camera_intrinsics.get(camera_key, {}) if isinstance(camera_intrinsics, dict) else {}

    if not isinstance(selected_raw, dict):
        selected_raw = {}

    camera_matrix = _to_camera_matrix(selected_raw.get("camera_matrix"), defaults["camera_matrix"])
    dist_coeffs = _to_dist_coeffs(selected_raw.get("dist_coeffs"), defaults["dist_coeffs"])
    return camera_matrix, dist_coeffs


def load_intrinsics_for_camera(camera_id: int, config_path: Path | str | None = None) -> tuple[np.ndarray, np.ndarray]:
    if config_path is None:
        from app.core.settings import get_settings

        resolved_path = get_settings().config_file
    else:
        resolved_path = Path(config_path)

    payload: dict[str, Any] = {}
    try:
        if resolved_path.exists():
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}

    return intrinsics_from_payload(payload, camera_id=camera_id)
