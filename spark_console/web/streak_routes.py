"""Owned, read-only relationship metadata endpoints."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from spark_console.db import session_scope
from spark_console.services import NotFound, ValidationError
from spark_console.services.streaks import list_streaks, streak_history


def build_streak_router(engine, auth):
    router = APIRouter()

    def respond(request, callback, **values):
        with session_scope(engine) as db:
            user, _ = auth.current(request, db)
            try:
                result = callback(db, user, **values)
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except ValidationError as error:
                raise HTTPException(422, str(error)) from error
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/api/streaks")
    def index(request: Request, account_id: str | None = None, task_id: str | None = None,
              filter: Literal["all", "active", "has_streak", "needs_attention", "without_task"] = "all",
              order: Literal["days_desc", "name"] = "days_desc",
              offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100)):
        return respond(request, list_streaks, account_id=account_id, task_id=task_id,
                       filter=filter, order=order, offset=offset, limit=limit)

    @router.get("/api/streaks/history")
    def history(request: Request, account_id: str, sec_uid: str = Query(min_length=1, max_length=256),
                offset: int = Query(0, ge=0, le=100000), limit: int = Query(30, ge=1, le=100)):
        return respond(request, streak_history, account_id=account_id, sec_uid=sec_uid, offset=offset, limit=limit)

    return router
