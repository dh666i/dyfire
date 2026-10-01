from sqlalchemy.orm import Session

from spark_console.models import AuditEvent
from spark_console.web.request_context import current_audit_context


class AuditService:
    def __init__(self, session: Session):
        self.session = session

    def write(
        self,
        actor_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        detail: str | None = None,
        *,
        outcome: str | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> AuditEvent:
        request_context = current_audit_context()
        if outcome is None:
            outcome = (
                "failure"
                if action.endswith((".failed", ".unavailable", ".error"))
                else "success"
            )
        if outcome not in {"success", "failure", "unknown"}:
            outcome = "unknown"
        if outcome == "failure" and not error_code:
            # Existing call sites predate the explicit error_code argument;
            # retain a searchable failure reason without guessing a secret or
            # copying arbitrary detail text into a new field.
            error_code = action
        event = AuditEvent(
            actor_user_id=actor_user_id,
            action=action[:64],
            resource_type=resource_type[:32],
            resource_id=resource_id,
            detail=detail[:240] if detail else None,
            request_id=request_id or request_context.get("request_id"),
            client_ip=client_ip or request_context.get("client_ip"),
            outcome=outcome[:16] if outcome else None,
            error_code=error_code[:64] if error_code else None,
        )
        self.session.add(event)
        return event
