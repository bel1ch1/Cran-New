from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette import status

from app.core.security import get_current_user, is_authenticated
from app.dependencies import get_templates

router = APIRouter(tags=["pages"])


def _protected_template(request: Request, template_name: str):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"user": get_current_user(request)},
    )


@router.get("/")
async def root(request: Request):
    target = "/main" if is_authenticated(request) else "/login"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/main")
async def main_page(request: Request):
    return _protected_template(request, "main.html")


@router.get("/bridge-calibration")
async def bridge_calibration_page(request: Request):
    return _protected_template(request, "xy_settings.html")


@router.get("/hook-calibration")
async def hook_calibration_page(request: Request):
    return _protected_template(request, "z_settings.html")


@router.get("/statistics")
async def statistics_page(request: Request):
    return _protected_template(request, "dashboard.html")


@router.get("/management")
async def management_page(request: Request):
    return _protected_template(request, "control.html")


@router.get("/xy-settings")
async def xy_settings_page(request: Request):
    return _protected_template(request, "xy_settings.html")


@router.get("/xy-calib-640x480")
@router.get("/xy-calib-1920x1080")
async def xy_calibration_stream_page(request: Request):
    return _protected_template(request, "xy_calib.html")


@router.get("/z-settings")
async def z_settings_page(request: Request):
    return _protected_template(request, "z_settings.html")


@router.get("/z-calib")
async def z_calibration_stream_page(request: Request):
    return _protected_template(request, "z_calib.html")


@router.get("/control")
async def control_page(request: Request):
    return _protected_template(request, "control.html")


@router.get("/calibration-complete")
async def calibration_complete_page(request: Request):
    return _protected_template(request, "calibration_complete.html")

