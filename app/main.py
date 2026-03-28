import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.core.settings import get_settings
from app.routers import api, auth, pages
from app.services.external_pose_processes import ensure_pose_supervisor_scripts_running

logger = logging.getLogger(__name__)


def _is_regular_pose_config_valid(config_path: Path) -> tuple[bool, str]:
    try:
        # Use the same loaders as regular pose scripts to guarantee compatibility.
        from bridge_pose_modbus import load_bridge_runtime_config
        from hook_pose_modbus import load_hook_runtime_config

        load_bridge_runtime_config(config_path)
        load_hook_runtime_config(config_path)
        return True, ""
    except Exception as exc:
        return False, str(exc)


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

    @app.on_event("startup")
    async def _startup_sync_pose_mode() -> None:
        is_valid, reason = _is_regular_pose_config_valid(settings.config_file)
        if is_valid:
            # Ensure regular pose loop is active by default after app restarts.
            # This clears a stale lock file left by an interrupted calibration session.
            ensure_pose_supervisor_scripts_running()
            logger.info("Regular pose mode enabled on startup (config is valid).")
            return

        logger.warning(
            "Regular pose mode not enabled on startup: calibration config is not ready (%s).",
            reason or "unknown validation error",
        )

    return app


app = create_app()

