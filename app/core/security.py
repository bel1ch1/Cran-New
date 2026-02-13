from typing import Any

from fastapi import Request


def get_current_user(request: Request) -> str | None:
    user = request.session.get("user")
    return str(user) if user else None


def is_authenticated(request: Request) -> bool:
    return get_current_user(request) is not None


def login_user(request: Request, username: str) -> None:
    request.session["user"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def auth_payload(request: Request) -> dict[str, Any]:
    return {"authenticated": is_authenticated(request), "user": get_current_user(request)}

