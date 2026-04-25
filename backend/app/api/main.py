from fastapi import APIRouter

from app.api.routes import (
    admin,
    auth,
    items,
    login,
    private,
    sessions,
    teams,
    users,
    utils,
    ws,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(teams.router)
api_router.include_router(admin.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(ws.router)
api_router.include_router(sessions.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
