import asyncio

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette import status

from app.core.security import is_authenticated
from app.dependencies import get_bridge_runtime, get_config_store, get_hook_runtime
from app.schemas.calibration import CommandResponse, XYMarkerSettings, ZMarkerSettings
from app.services.config_store import ConfigStore
from app.services.control_service import get_command_message

router = APIRouter(tags=["api"])


def _require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")


def _store() -> ConfigStore:
    return get_config_store()


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
    _store().update_bridge_settings(marker_size=payload.marker_size)
    return {"message": "Настройки калибровки моста сохранены"}


@router.get("/xy-marker-settings")
async def xy_marker_settings_get(request: Request):
    _require_auth(request)
    return _store().get_bridge_settings()


@router.get("/calibration-data")
async def calibration_data(request: Request):
    _require_auth(request)
    return _store().get_calibration_data()


@router.post("/save-calibration")
async def save_calibration(request: Request):
    _require_auth(request)
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


@router.websocket("/ws/calibration/hook")
@router.websocket("/ws1")
async def hook_camera_stream(websocket: WebSocket):
    await websocket.accept()
    runtime = get_hook_runtime()
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
                runtime.handle_command(raw)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                return

            hook_settings = _store().get_hook_settings()
            marker_size_mm = hook_settings.get("marker_size_mm") or 100
            marker_id = hook_settings.get("marker_id")
            state = await runtime.tick(marker_size_mm=marker_size_mm, marker_id=marker_id)

            if runtime.last_frame_bytes:
                await websocket.send_bytes(runtime.last_frame_bytes)
            await websocket.send_json(
                {
                    "type": "calibration_state",
                    "data": state,
                }
            )
    except WebSocketDisconnect:
        return
    finally:
        runtime.close()


@router.websocket("/ws/calibration/bridge")
@router.websocket("/ws3")
async def bridge_camera_stream(websocket: WebSocket):
    await websocket.accept()
    runtime = get_bridge_runtime()
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
                runtime.handle_command(raw)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                return

            bridge_settings = _store().get_bridge_settings()
            marker_size_mm = bridge_settings.get("marker_size_mm") or 100
            state = await runtime.tick(marker_size_mm=marker_size_mm)
            _store().update_bridge_runtime_result(
                crane_x_m=state["crane_x_m"],
                trolley_y_m=state["trolley_y_m"],
                known_marker_count=state.get("known_marker_count"),
                calibration_quality=state.get("calibration_quality"),
                marker_positions_m=state.get("marker_positions_m"),
                roi_preview=state.get("roi_preview"),
                movement_direction=state.get("movement_direction"),
            )

            if runtime.last_frame_bytes:
                await websocket.send_bytes(runtime.last_frame_bytes)
            await websocket.send_json(
                {
                    "type": "xy_calibration_state",
                    "data": state,
                }
            )
    except WebSocketDisconnect:
        return
    finally:
        runtime.close()
