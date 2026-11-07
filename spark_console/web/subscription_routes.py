from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import select

from spark_console.db import session_scope
from spark_console.models import RedemptionCode, User, SubscriptionGrant, UserSubscription
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import (
    NEVER_EXPIRES_AT,
    SubscriptionService,
    pricing_plans,
    task_limit_label,
)
from spark_console.web.auth import WebAuth
from spark_console.web.membership import membership_summary


@dataclass(frozen=True)
class AdminRedemptionCode:
    code: RedemptionCode
    status: str
    used_by_username: str | None
    can_revoke: bool


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def admin_redemption_items(db) -> list[AdminRedemptionCode]:
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(RedemptionCode, User.username)
        .outerjoin(User, RedemptionCode.redeemed_by_user_id == User.id)
        .order_by(RedemptionCode.created_at.desc())
    ).all()
    items = []
    for code, username in rows:
        if code.redeemed_at is not None:
            status = "已兑换"
        elif code.revoked_at is not None:
            status = "已撤销"
        elif code.expires_at is not None and _aware(code.expires_at) < NEVER_EXPIRES_AT and _aware(code.expires_at) <= now:
            status = "已过期"
        else:
            status = "待兑换"
        items.append(
            AdminRedemptionCode(
                code=code,
                status=status,
                used_by_username=username,
                can_revoke=status == "待兑换",
            )
        )
    return items


def admin_membership_map(db, users) -> dict[str, dict[str, object]]:
    return {user.id: membership_summary(db, user) for user in users}


def build_subscription_router(engine, auth: WebAuth, page, render_admin, cipher=None) -> APIRouter:
    router = APIRouter()

    @router.get("/subscription")
    def subscription_page(request: Request):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            return page(
                request,
                "subscription.html",
                title="会员订阅",
                grants=db.scalars(select(SubscriptionGrant).where(SubscriptionGrant.user_id==user.id).order_by(SubscriptionGrant.created_at.desc()).limit(20)).all(),
                plans=pricing_plans(),
                redeemed=request.query_params.get("redeemed") == "1",
                required=request.query_params.get("required") == "1",
                **context,
            )

    @router.post("/subscription/redeem")
    def redeem_subscription(
        request: Request,
        csrf_token: str = Form(default=""),
        code: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            user, record, context = auth.user_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).redeem(user.id, code)
            except (ValidationError, Conflict) as error:
                context["membership"] = membership_summary(db, user)
                return page(
                    request,
                    "subscription.html",
                    status_code=400,
                    title="会员订阅",
                    plans=pricing_plans(),
                    error=str(error),
                    redeemed=False,
                    required=False,
                    **context,
                )
        return RedirectResponse("/subscription?redeemed=1", status_code=303)

    @router.post("/api/subscription/redeem")
    async def redeem_subscription_api(request: Request):
        try:
            payload = await request.json()
        except (TypeError, ValueError):
            raise HTTPException(400, "请求格式无效") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "请求格式无效")
        code = str(payload.get("code") or "").strip()
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, payload.get("csrf_token", ""))
            try:
                subscription = SubscriptionService(db, AuditService(db)).redeem(user.id, code)
            except (ValidationError, Conflict) as error:
                return JSONResponse({"ok": False, "message": str(error)}, status_code=400)
            summary = membership_summary(db, user)
            return JSONResponse({
                "ok": True,
                "message": "兑换成功",
                "membership": {
                    "active": summary.get("active"),
                    "status_label": summary.get("status_label"),
                    "days_remaining": summary.get("days_remaining"),
                    "max_accounts": summary.get("max_accounts"),
                    "max_tasks": summary.get("max_tasks"),
                    "task_limit_label": summary.get("task_limit_label"),
                },
            })

    @router.post('/subscription/preview')
    def preview_redemption(request:Request,csrf_token:str=Form(''),code:str=Form('')):
        with session_scope(engine) as db:
            user,record=auth.current(request,db);auth.csrf(record,csrf_token)
            from spark_console.subscription_preview import preview
            try:value=preview(db,user.id,code)
            except ValidationError as error:return JSONResponse({'message':str(error)},status_code=400)
            from zoneinfo import ZoneInfo
            until=datetime.fromisoformat(value['valid_until']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
            effect='本期到期后生效' if value['quota_effect']=='after_current_term' else '立即生效'
            return {'preview':value,'message':f"增加 {value['duration_days']} 天，有效期预计至 {until}（北京时间）。账号额度 {value['max_accounts']}，任务额度 {task_limit_label(value['max_tasks'])}，{effect}，额度不叠加。"}

    @router.post("/admin/redemption-codes")
    def create_redemption_codes(
        request: Request,
        csrf_token: str = Form(default=""),
        duration_days: int = Form(default=30),
        max_accounts: int = Form(default=1),
        max_tasks: int = Form(default=10),
        plan_id: str = Form(default=""),
        quantity: int = Form(default=1),
    ):
        with session_scope(engine) as db:
            admin, record, context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            if not 1 <= quantity <= 20:
                raise HTTPException(400, "每次最多生成 20 个兑换码")
            service = SubscriptionService(db, AuditService(db), cipher=cipher)
            try:
                plaintext_codes = [
                    service.create_redemption_code(
                        admin.id,
                        duration_days=duration_days,
                        max_accounts=max_accounts,
                        max_tasks=max_tasks,
                        plan_id=plan_id or None,
                    )[1]
                    for _ in range(quantity)
                ]
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
            return render_admin(
                request, db, context, one_time_codes=plaintext_codes
            )

    @router.post("/admin/redemption-codes/{code_id}/revoke")
    def revoke_redemption_code(
        request: Request,
        code_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).revoke_redemption_code(
                    admin.id, code_id
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except Conflict as error:
                raise HTTPException(409, str(error)) from error
        return RedirectResponse("/admin/codes", status_code=303)

    @router.post("/admin/users/{user_id}/subscription")
    def extend_user_subscription(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
        duration_days: int = Form(default=30),
        max_accounts: int = Form(default=1),
        max_tasks: int = Form(default=10),
        plan_id: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).extend(
                    admin.id,
                    user_id,
                    duration_days=duration_days,
                    max_accounts=max_accounts,
                    max_tasks=max_tasks,
                    plan_id=plan_id or None,
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/subscriptions", status_code=303)

    @router.post("/admin/users/{user_id}/subscription/revoke")
    def revoke_user_subscription(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).revoke(
                    admin.id, user_id
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except (Conflict, ValidationError) as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/subscriptions", status_code=303)

    return router
