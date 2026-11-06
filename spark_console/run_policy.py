"""Canonical run labels and safe customer actions, shared by API and compatibility routes."""
RUN_STATUS_LABELS = {
    "running": "执行中",
    "success": "已发出",
    "submitted_unconfirmed": "待核实",
    "failed": "未发出",
    "skipped": "已跳过",
    "retrying": "等待重试",
}
RUN_STAGE_LABELS = {
    "starting": "准备执行",
    "authenticating": "验证登录",
    "selecting_target": "查找好友",
    "worker_error": "执行异常",
    "sending": "发送消息",
    "confirming": "确认送达",
    "submitted": "已提交发送",
    "claimed": "已领取",
    "complete": "已完成",
    "missed_startup": "执行器离线",
    "late": "超过发送窗口",
    "clock_check": "时间校验",
    "entitlement_check": "会员校验",
    "navigation": "打开聊天页",
    "message_input": "填写消息",
    "delivery_confirmation": "确认送达",
    "account_queue": "等待账号",
    "target_bound": "绑定好友",
    "target_search": "查找好友",
}

LOGIN_ERROR_CODES = {"cookie_invalid", "credential_invalid", "account_verification_required"}
MISS_ERROR_CODES = {
    "health_retry_waiting",
    "health_retry_expired",
    "health_retry_exhausted",
    "health_retry_cancelled",
    "health_retry_task_changed", "health_retry_entitlement", "health_retry_account_unavailable",
    "health_retry_account_verification", "health_retry_reauth", "health_retry_health_pending",
    "account_health_pending",
    "account_health_blocked",
    "account_busy",
    "missed_window",
    "worker_was_offline",
    "target_ambiguous",
}

def run_view_status(run) -> dict[str, str]:
    status = str(getattr(run, "status", "") or "")
    error_code = str(getattr(run, "error_code", "") or "")
    if status == "success":
        return {"label": "已发出", "tone": "good", "kind": "success"}
    if status == "submitted_unconfirmed" or error_code == "delivery_confirmation_unavailable":
        return {"label": "待核实", "tone": "warn", "kind": "unconfirmed"}
    if error_code in LOGIN_ERROR_CODES:
        return {"label": "需要验证", "tone": "danger", "kind": "login"}
    if error_code == "account_busy":
        return {"label": "等待账号", "tone": "warn", "kind": "account_queue"}
    if error_code == "target_ambiguous":
        return {"label": "目标需确认", "tone": "danger", "kind": "failed"}
    if status == "retrying" or error_code.startswith("retry_scheduled_") or error_code == "health_retry_waiting":
        return {"label": "等待重试", "tone": "warn", "kind": "retry"}
    if status == "running":
        return {"label": "执行中", "tone": "progress", "kind": "running"}
    if error_code == "subscription_inactive":
        return {"label": "会员不可用", "tone": "danger", "kind": "failed"}
    if error_code in MISS_ERROR_CODES:
        return {"label": "本次未发出", "tone": "warn", "kind": "missed"}
    if status == "skipped":
        return {"label": "本次未发出", "tone": "warn", "kind": "missed"}
    if status == "failed":
        return {"label": RUN_STATUS_LABELS.get(status, "失败"), "tone": "danger", "kind": "failed"}
    return {"label": RUN_STATUS_LABELS.get(status, status or "未知"), "tone": "off", "kind": "other"}


def run_guidance(run) -> dict[str, str] | None:
    code = str(getattr(run, "error_code", "") or "")
    from spark_console.health_retry import WINDOW
    window_minutes = int(WINDOW.total_seconds() // 60)
    submitted = getattr(run, "submitted_at", None) is not None or str(getattr(run, "status", "")) == "submitted_unconfirmed"
    if code in {"account_identity_unavailable", "account_identity_mismatch"}:
        return {'text':'本次尚未发送消息。账号身份暂时无法确认，请在抖音账号页完成核对；原任务与登录资料已保留。', 'action':'account'}
    if code == 'health_retry_waiting':
        return {'text':f'这次还没发出去，多半是聊天页暂时没加载完，不必重新扫码。系统会在原计划后 {window_minutes} 分钟内继续重试。', 'action':'none'}
    if code in {'health_retry_task_changed', 'health_retry_account_unavailable'}:
        return {'text':'任务设置或绑定关系已经变化，本次恢复已停止。请检查当前任务设置。','action':'task'}
    if code == 'health_retry_entitlement':
        return {'text':'当前用户或任务执行额度不可用，请检查账号状态与订阅。','action':'subscription'}
    if code in {'health_retry_account_verification', 'health_retry_reauth', 'health_retry_health_pending'}:
        return {'text':'账号状态或登录资料已变化，本次恢复已停止。请在抖音账号页查看实际状态。','action':'account'}
    if code == 'health_retry_cancelled':
        return {'text':'此历史记录的恢复重试已取消，请结合账号状态和任务设置核对原因。','action':'none'}
    if code in {'health_retry_expired', 'health_retry_exhausted'} and not submitted:
        return {'text':'本次恢复窗口或尝试次数已用尽，消息没有提交。确认任务、账号与额度正常后，可申请安全重试。','action':'retry'}
    if code in {'health_retry_expired', 'health_retry_exhausted', 'health_retry_cancelled'}:
        return {'text':'本次恢复重试已结束。请查看账号状态；下一天的正常计划不受影响。', 'action':'account'}
    if code == 'health_retry_submitted':
        return {'text':'消息已进入提交步骤，为避免重复消息不会自动重发，请核对抖音聊天记录。', 'action':'none'}
    if code in {'account_health_pending', 'account_health_blocked'}:
        return {'text':'账号暂缓发送，正在自动复核。通常不用重新扫码；需要验证时会在账号页标明。恢复后执行后续计划，本次不自动补发。', 'action':'account'}
    if code == 'account_verification_required':
        return {'text':'抖音要求安全验证，请前往账号页完成验证。系统不会自动尝试密码或验证码。', 'action':'account'}
    if code == 'account_busy':
        return {'text':'正在等待该账号的其他云端操作完成，无需重复操作。', 'action':'none'}
    if code.startswith("retry_scheduled_"):
        return {"text": "临时故障，系统已安排自动重试，无需重复创建任务。", "action": "none"}
    if code in {"cookie_invalid", "credential_invalid"}:
        return {"text": "请在账号页查看复核结果或重新登录；已启用任务将在恢复后继续后续计划。", "action": "account"}
    if code == "subscription_inactive":
        return {"text": "会员当前不可用，续费后可继续执行后续计划。", "action": "subscription"}
    if code in {"target_not_found", "conversation_not_opened"}:
        return {"text": "请检查目标好友，并从该账号的最近会话中重新选择。", "action": "task"}
    if code == "target_ambiguous":
        return {"text": "同步好友中存在多个同名目标。请编辑任务并重新选择具体好友，避免发错人。", "action": "task"}
    if code in {"account_unavailable", "task_inactive"}:
        return {"text": "发送账号或任务当前不可用，请检查任务设置。", "action": "task"}
    if code in {"worker_was_offline", "missed_window"}:
        return {"text": "本次已错过发送窗口且不会补发，后续每日计划不受影响。", "action": "none"}
    if code == "delivery_confirmation_unavailable":
        return {
            "text": "系统已经执行发送，但未能从抖音页面取得确认。为避免重复消息，本次不会自动重发，请先到抖音聊天记录核对。",
            "action": "none",
        }
    if code == "system_time_unhealthy":
        return {"text": "服务器时间异常，请联系管理员处理。", "action": "none"}
    if code in {"automation_failed", "unexpected_error"}:
        return {"text": "页面操作未完成；如连续出现，请联系管理员并提供本次执行时间。", "action": "none"}
    if code == "page_timeout":
        return {"text": "抖音页面暂时没有完成加载，系统会在安全窗口内自动重试。", "action": "none"}
    if code == "browser_unavailable":
        return {"text": "云端浏览器连接暂时中断，系统会自动重试；连续出现时请复核账号状态。", "action": "none"}
    if code == "message_input_failed":
        return {"text": "消息还没有进入提交步骤，系统会自动重试，不会重复发送。", "action": "none"}
    if code == "delivery_confirmation_timeout":
        return {"text": "消息已经进入发送步骤，但页面确认超时。请先核对聊天记录，系统不会自动重发。", "action": "none"}
    return None


