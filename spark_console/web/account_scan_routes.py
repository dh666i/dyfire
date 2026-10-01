from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Lock

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from spark_console.crypto import CookieCipher
from spark_console.db import session_scope
from spark_console.models import utc_now
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.scan_sessions import (
    ScanSessionService,
    browser_view_revision,
)
from spark_console.services.subscriptions import SubscriptionService
from spark_console.redis_backend import RedisBackend
from spark_console.rate_limit import RedisRequestLimiter
from spark_console.web.auth import WebAuth


NO_STORE = {"Cache-Control": "no-store"}
START_LIMIT = 5
STATUS_LIMIT = 90
QR_LIMIT = 90
CANCEL_LIMIT = 10
INTERACT_LIMIT = 120
OTP_LIMIT = 5


class ScanRequestLimiter:
    """Small per-process limiter for authenticated scan API traffic."""

    def __init__(self, window: timedelta = timedelta(minutes=1), now=utc_now):
        self.window = window
        self.now = now
        self._requests: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int) -> bool:
        with self._lock:
            values = self._requests[key]
            current = self.now()
            cutoff = current - self.window
            while values and values[0] <= cutoff:
                values.popleft()
            if len(values) >= limit:
                return False
            values.append(current)
            return True


def _error(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "message": message},
        status_code=status_code,
        headers=NO_STORE,
    )


def build_account_scan_router(
    engine,
    auth: WebAuth,
    cipher: CookieCipher,
    limiter: ScanRequestLimiter | None = None,
    redis_backend: RedisBackend | None = None,
) -> APIRouter:
    router = APIRouter()
    if limiter is not None:
        request_limiter = limiter
    elif redis_backend is not None and redis_backend.enabled:
        request_limiter = RedisRequestLimiter(
            redis_backend, namespace="scan", window_seconds=60
        )
    else:
        request_limiter = ScanRequestLimiter()

    def limited(user_id: str, action: str, limit: int) -> JSONResponse | None:
        if request_limiter.allow(f"{user_id}:{action}", limit):
            return None
        return _error(429, "rate_limited", "请求过于频繁，请稍后重试")

    @router.post("/accounts/scan")
    def start_scan(
        request: Request,
        csrf_token: str = Form(default=""),
        account_id: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            if response := limited(user.id, "start", START_LIMIT):
                return response
            target_account_id = account_id.strip() or None
            subscription = SubscriptionService(db, AuditService(db))
            if target_account_id is not None:
                try:
                    AccountService(db, cipher, AuditService(db)).get_owned(
                        user.id, target_account_id
                    )
                except NotFound:
                    return _error(404, "not_found", "未找到抖音账号")
                if user.role != "admin" and not subscription.active(user.id):
                    return _error(
                        403,
                        "subscription_inactive",
                        "会员未开通或已过期，请开通后重试",
                    )
            elif user.role != "admin":
                try:
                    subscription.require_account_capacity(user.id)
                except ValidationError:
                    return _error(
                        403,
                        "subscription_inactive",
                        "会员未开通或已过期，请开通后重试",
                    )
                except Conflict:
                    return _error(
                        409,
                        "account_limit_reached",
                        "抖音账号额度已用完，请升级套餐或删除旧账号",
                    )
            service = ScanSessionService(db)
            service.expire_stale()
            try:
                scan = service.start(user.id, target_account_id)
            except Conflict as error:
                if str(error)=='queue_full':return _error(429,'queue_full','当前绑定队列已满，请稍后再试，现有账号和任务不受影响')
                scan = service.active_owned(user.id)
                if scan is None:
                    return _error(409, "slot_busy", "扫码通道正在使用，请稍后重试")
                if scan.account_id != target_account_id:
                    return _error(
                        409,
                        "scan_target_conflict",
                        "已有其他账号登录会话正在进行，请先完成或取消后再试",
                    )
                projection = service.public_status(scan)
                return JSONResponse(projection, status_code=200, headers=NO_STORE)
            projection = service.public_status(scan)
        return JSONResponse(projection, status_code=201, headers=NO_STORE)

    @router.post("/accounts/recovery")
    def start_recovery(
        request: Request,
        account_id: str = Form(default=""),
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            if response := limited(user.id, "start", START_LIMIT):
                return response
            if user.role != "admin" and not SubscriptionService(db, AuditService(db)).active(user.id):
                return _error(403, "subscription_inactive", "会员未开通或已过期，请开通后重试")
            service = ScanSessionService(db)
            try:
                scan = service.start_recovery(user.id, account_id.strip())
            except NotFound:
                return _error(404, "not_found", "未找到抖音账号")
            except Conflict:
                return _error(409, "slot_busy", "复核通道正在使用，请稍后重试")
            projection = service.public_status(scan)
        return JSONResponse(projection, status_code=201, headers=NO_STORE)

    @router.get("/accounts/scan/{scan_id}")
    def scan_status(request: Request, scan_id: str):
        with session_scope(engine) as db:
            user, _record = auth.current(request, db)
            if response := limited(user.id, "status", STATUS_LIMIT):
                return response
            service = ScanSessionService(db)
            service.expire_stale()
            try:
                scan = service.get_owned(user.id, scan_id)
            except NotFound:
                return _error(404, "not_found", "未找到扫码会话")
            projection = service.public_status(scan)
        return JSONResponse(projection, headers=NO_STORE)

    @router.get("/accounts/scan/{scan_id}/qr")
    def scan_qr(request: Request, scan_id: str):
        with session_scope(engine) as db:
            user, _record = auth.current(request, db)
            if response := limited(user.id, "qr", QR_LIMIT):
                return response
            service = ScanSessionService(db)
            service.expire_stale()
            try:
                scan = service.get_owned(user.id, scan_id)
            except NotFound:
                return _error(404, "not_found", "未找到扫码会话")
            png = scan.qr_png
            if png is None:
                return _error(404, "qr_unavailable", "二维码尚未就绪")
            revision = browser_view_revision(png)
            headers = dict(NO_STORE)
            if revision is not None:
                etag = f'"{revision}"'
                headers["ETag"] = etag
                headers["X-Frame-Revision"] = revision
                if request.headers.get("if-none-match") == etag:
                    return Response(status_code=304, headers=headers)
        return Response(content=png, media_type="image/png", headers=headers)

    @router.post("/accounts/scan/{scan_id}/cancel")
    def cancel_scan(
        request: Request, scan_id: str, csrf_token: str = Form(default="")
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            if response := limited(user.id, "cancel", CANCEL_LIMIT):
                return response
            service = ScanSessionService(db)
            service.expire_stale()
            try:
                scan = service.cancel_owned(user.id, scan_id)
            except NotFound:
                return _error(404, "not_found", "未找到扫码会话")
            except Conflict:
                return _error(409, "scan_not_active", "扫码会话已结束")
            projection = service.public_status(scan)
        return JSONResponse(projection, headers=NO_STORE)

    @router.post("/accounts/scan/{scan_id}/interact")
    def interact_scan(
        request: Request,
        scan_id: str,
        csrf_token: str = Form(default=""),
        kind: str = Form(default="click"),
        request_id: str = Form(default=""),
        text: str = Form(default=""),
        x: float = Form(default=-1),
        y: float = Form(default=-1),
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            if kind in {"click", "text"}:
                if kind == "text":
                    action, limit = f"otp:{scan_id}", OTP_LIMIT
                else:
                    action, limit = f"interact:{scan_id}", INTERACT_LIMIT
            else:
                return _error(400, "invalid_interaction", "交互请求无效")
            try:
                service = ScanSessionService(db)
                service.expire_stale()
                if kind == "text":
                    # Idempotent retries must remain readable even after the
                    # per-minute limiter is exhausted. A syntactically valid
                    # request_id that was already accepted is returned before
                    # considering a new queue mutation.
                    pending = service.get_text_interaction_by_request_owned(
                        user.id, scan_id, request_id
                    )
                    if pending is None:
                        active = service.active_text_interaction_owned(
                            user.id, scan_id
                        )
                        if active is not None:
                            return JSONResponse(
                                {
                                    "error": "interaction_in_progress",
                                    "message": "上一条内容仍在发送，请等待结果后再试",
                                    **service.public_text_interaction(active),
                                },
                                status_code=409,
                                headers=NO_STORE,
                            )
                        if response := limited(user.id, action, limit):
                            return response
                        pending = service.queue_text(
                            user.id,
                            scan_id,
                            text,
                            cipher,
                            request_id=request_id,
                        )
                    projection = {
                        "accepted": True,
                        **service.public_text_interaction(pending),
                    }
                elif kind == "click":
                    if response := limited(user.id, action, limit):
                        return response
                    service.queue_click(user.id, scan_id, x, y)
            except NotFound:
                return _error(404, "not_found", "未找到扫码会话")
            except ValidationError as error:
                if str(error) == "invalid_request_id":
                    return _error(
                        400,
                        "invalid_request_id",
                        "请求标识无效，请重新提交",
                    )
                return _error(400, "invalid_interaction", "交互请求无效")
            except Conflict as error:
                if str(error) == "interaction_in_progress":
                    active = service.active_text_interaction_owned(
                        user.id, scan_id
                    )
                    if active is not None:
                        return JSONResponse(
                            {
                                "error": "interaction_in_progress",
                                "message": "上一条内容仍在发送，请等待结果后再试",
                                **service.public_text_interaction(active),
                            },
                            status_code=409,
                            headers=NO_STORE,
                        )
                return _error(409, "scan_not_active", "扫码会话已结束")
        if kind == "text":
            return JSONResponse(projection, status_code=202, headers=NO_STORE)
        return JSONResponse({"accepted": True}, status_code=202, headers=NO_STORE)

    @router.get(
        "/accounts/scan/{scan_id}/interactions/{interaction_id}"
    )
    def text_interaction_status(
        request: Request,
        scan_id: str,
        interaction_id: int,
    ):
        with session_scope(engine) as db:
            user, _record = auth.current(request, db)
            if response := limited(
                user.id, f"interaction-status:{scan_id}", STATUS_LIMIT
            ):
                return response
            service = ScanSessionService(db)
            try:
                pending = service.get_text_interaction_owned(
                    user.id, scan_id, interaction_id
                )
            except NotFound:
                return _error(404, "not_found", "未找到交互请求")
            projection = service.public_text_interaction(pending)
        return JSONResponse(projection, headers=NO_STORE)

    @router.post("/accounts/{account_id}/rename")
    def rename_account(
        request: Request,
        account_id: str,
        csrf_token: str = Form(default=""),
        display_name: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            try:
                AccountService(db, cipher, AuditService(db)).rename_owned(
                    user.id, account_id, display_name
                )
            except NotFound:
                return _error(404, "not_found", "未找到抖音账号")
            except ValidationError:
                return _error(400, "invalid_display_name", "账号名称须为 1–64 个字符")
        return RedirectResponse("/accounts", status_code=303)

    return router
