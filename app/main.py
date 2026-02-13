from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.core.settings import get_settings
from app.routers import api, auth, pages


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0")

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="cran_session",
        max_age=60 * 60 * 8,
        same_site="lax",
        https_only=False,
    )

    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(api.router)
    return app


app = create_app()

