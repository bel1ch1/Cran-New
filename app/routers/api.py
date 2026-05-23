import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette import status

from app.core.security import is_authenticated
from app.core.settings import get_settings
from app.dependencies import (
    get_bridge_runtime,
    get_config_store,
    get_hook_runtime,
)
from app.schemas.calibration import CommandResponse, XYMarkerSettings, ZMarkerSettings
from app.services.calibration_runtime import BridgeCalibrationRuntime, HookCalibrationRuntime
from app.services.config_store import ConfigStore
from app.services.control_service import get_command_message
from app.services.influx_pose_reader import InfluxPoseConfig, read_pose_history_from_influx
from app.services.modbus_pose_reader import ModbusPoseReaderConfig, read_pose_values

router = APIRouter(tags=["api"])


def _require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")


def _store() -> ConfigStore:
    return get_config_store()


async def _run_calibration_websocket(
    websocket: WebSocket,
    runtime: BridgeCalibrationRuntime | HookCalibrationRuntime,
    *,
    state_type: str,
    build_state: Callable[[], Awaitable[dict]],
) -> None:
    await websocket.accept()
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                runtime.handle_command(raw)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                return

            state = await build_state()
            if runtime.last_frame_bytes:
                await websocket.send_bytes(runtime.last_frame_bytes)

            camera_error = getattr(runtime.camera_provider, "last_error", None)
            if camera_error:
                state = dict(state)
                state["camera_error"] = camera_error

            await websocket.send_json({"type": state_type, "data": state})
    except WebSocketDisconnect:
        return
    finally:
        runtime.detach_stream()


@router.post("/z-marker-settings")
async def z_marker_settings(payload: ZMarkerSettings, request: Request):
    _require_auth(request)
    _store().update_hook_settings(marker_size=payload.marker_size, marker_id=payload.marker_id)
    return {"message": "Настройки калибровки крюка сохранены"}


@router.get("/z-marker-settings")
async def z_marker_settings_get(request: Request):
    _require_auth(request)
    return _store().get_hook_settings()


@router.post("/xy-marker-settings")
async def xy_marker_settings(payload: XYMarkerSettings, request: Request):
    _require_auth(request)
    _store().update_bridge_settings(
        marker_size=payload.marker_size,
        zero_marker_offset_m=payload.zero_marker_offset_m,
    )
    return {"message": "Настройки калибровки моста сохранены"}


@router.get("/xy-marker-settings")
async def xy_marker_settings_get(request: Request):
    _require_auth(request)
    return _store().get_bridge_settings()


@router.get("/calibration-data")
async def calibration_data(request: Request):
    _require_auth(request)
    runtime_state = get_bridge_runtime().last_state or {}
    if not runtime_state:
        return _store().get_calibration_data()

    stored = _store().get_calibration_data()
    merged = dict(stored)
    merged["marker_positions_m"] = runtime_state.get("marker_positions_m") or stored.get("marker_positions_m", {})
    merged["roi_preview"] = runtime_state.get("roi_preview") or stored.get("roi_preview", {})
    merged["movement_direction"] = runtime_state.get("movement_direction") or stored.get("movement_direction", "unknown")
    merged["result"] = dict(stored.get("result", {}))
    merged["result"]["crane_x_m"] = runtime_state.get("crane_x_m")
    merged["result"]["trolley_y_m"] = runtime_state.get("trolley_y_m")
    if runtime_state.get("known_marker_count") is not None:
        merged["result"]["known_marker_count"] = runtime_state.get("known_marker_count")
    if runtime_state.get("calibration_quality") is not None:
        merged["result"]["calibration_quality"] = runtime_state.get("calibration_quality")
    return merged


@router.get("/statistics/modbus-pose")
async def statistics_modbus_pose(request: Request):
    _require_auth(request)
    settings = get_settings()
    return read_pose_values(
        ModbusPoseReaderConfig(
            host=settings.modbus_host,
            port=settings.modbus_port,
            unit_id=settings.modbus_unit_id,
            bridge_base_register=settings.modbus_bridge_base_register,
            hook_base_register=settings.modbus_hook_base_register,
        )
    )


@router.get("/statistics/modbus-history")
async def statistics_modbus_history(request: Request):
    _require_auth(request)
    settings = get_settings()
    return read_pose_history_from_influx(
        InfluxPoseConfig(
            url=settings.influx_url,
            org=settings.influx_org,
            bucket=settings.influx_bucket,
            token=settings.influx_token,
            measurement=settings.influx_measurement,
            field_bridge_x=settings.influx_field_bridge_x,
            field_bridge_y=settings.influx_field_bridge_y,
            field_hook_distance=settings.influx_field_hook_distance,
        )
    )


@router.post("/save-calibration")
async def save_calibration(request: Request):
    _require_auth(request)
    runtime_state = get_bridge_runtime().last_state or {}
    if runtime_state:
        _store().update_bridge_runtime_result(
            crane_x_m=float(runtime_state.get("crane_x_m") or 0.0),
            trolley_y_m=float(runtime_state.get("trolley_y_m") or 0.0),
            known_marker_count=runtime_state.get("known_marker_count"),
            calibration_quality=runtime_state.get("calibration_quality"),
            marker_positions_m=runtime_state.get("marker_positions_m"),
            roi_preview=runtime_state.get("roi_preview"),
            movement_direction=runtime_state.get("movement_direction"),
        )
    data = _store().save_bridge_calibration()
    return {"message": "Конфигурационный JSON обновлен", "data": data}


@router.post("/calibration/bridge/start")
async def start_bridge_calibration(request: Request):
    _require_auth(request)
    get_bridge_runtime().is_calibration_running = True
    return {"message": "Калибровка моста запущена"}


@router.post("/calibration/bridge/stop")
async def stop_bridge_calibration(request: Request):
    _require_auth(request)
    get_bridge_runtime().is_calibration_running = False
    return {"message": "Калибровка моста остановлена"}


@router.post("/calibration/hook/start")
async def start_hook_calibration(request: Request):
    _require_auth(request)
    get_hook_runtime().is_calibration_running = True
    return {"message": "Калибровка крюка запущена"}


@router.post("/calibration/hook/stop")
async def stop_hook_calibration(request: Request):
    _require_auth(request)
    get_hook_runtime().is_calibration_running = False
    return {"message": "Калибровка крюка остановлена"}


@router.post("/start_z_regular", response_model=CommandResponse)
@router.post("/stop_z_regular", response_model=CommandResponse)
@router.post("/start_xy_regular", response_model=CommandResponse)
@router.post("/stop_xy_regular", response_model=CommandResponse)
@router.post("/restart", response_model=CommandResponse)
async def control_commands(request: Request):
    _require_auth(request)
    command = request.url.path.lstrip("/")
    _store().update_last_command(command)
    return {"command": command, "message": get_command_message(command)}


async def _serve_calibration_stream(
    websocket: WebSocket,
    *,
    get_runtime: Callable[[], BridgeCalibrationRuntime | HookCalibrationRuntime],
    state_type: str,
    build_state: Callable[[BridgeCalibrationRuntime | HookCalibrationRuntime], Awaitable[dict]],
) -> None:
    runtime = get_runtime()

    async def tick_state() -> dict:
        return await build_state(runtime)

    await _run_calibration_websocket(
        websocket,
        runtime,
        state_type=state_type,
        build_state=tick_state,
    )


@router.websocket("/ws/calibration/hook")
@router.websocket("/ws1")
async def hook_camera_stream(websocket: WebSocket):
    async def build_state(runtime: HookCalibrationRuntime) -> dict:
        hook_settings = _store().get_hook_settings()
        marker_size_mm = hook_settings.get("marker_size_mm") or 100
        marker_id = hook_settings.get("marker_id")
        return await runtime.tick(marker_size_mm=marker_size_mm, marker_id=marker_id)

    await _serve_calibration_stream(
        websocket,
        get_runtime=get_hook_runtime,
        state_type="calibration_state",
        build_state=build_state,
    )


@router.websocket("/ws/calibration/bridge")
@router.websocket("/ws3")
async def bridge_camera_stream(websocket: WebSocket):
    async def build_state(runtime: BridgeCalibrationRuntime) -> dict:
        bridge_settings = _store().get_bridge_settings()
        marker_size_mm = bridge_settings.get("marker_size_mm") or 100
        zero_marker_offset_m = float(bridge_settings.get("zero_marker_offset_m") or 0.0)
        return await runtime.tick(
            marker_size_mm=marker_size_mm,
            zero_marker_offset_m=zero_marker_offset_m,
        )

    await _serve_calibration_stream(
        websocket,
        get_runtime=get_bridge_runtime,
        state_type="xy_calibration_state",
        build_state=build_state,
    )
