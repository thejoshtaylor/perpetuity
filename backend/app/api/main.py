from fastapi import APIRouter

from app.api.routes import (
    admin,
    auth,
    github,
    github_webhooks,
    items,
    login,
    private,
    projects,
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
api_router.include_router(github.router)
api_router.include_router(github_webhooks.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(ws.router)
api_router.include_router(sessions.router)
api_router.include_router(projects.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
