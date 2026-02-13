from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette import status

from app.core.security import is_authenticated, login_user, logout_user
from app.core.settings import get_settings
from app.dependencies import get_templates

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/main", status_code=status.HTTP_303_SEE_OTHER)
    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@router.post("/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    settings = get_settings()
    if username == settings.auth_user and password == settings.auth_password:
        login_user(request, username)
        return RedirectResponse(url="/main", status_code=status.HTTP_303_SEE_OTHER)

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Неверный логин или пароль"},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@router.get("/logout")
async def logout_action(request: Request):
    logout_user(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

