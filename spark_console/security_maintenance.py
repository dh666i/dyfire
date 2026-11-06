"""Bounded sensitive-material cleanup. No backup or live credential rotation."""
from datetime import datetime, timezone
from sqlalchemy import select, delete, update
from spark_console.models import MailPreference, PasswordReset, MailDelivery, RedemptionCode
from spark_console.mail_outbox import _context, QUEUED_STATUSES


def maintain(db, cipher, *, apply=False, limit=500, now=None):
    now = now or datetime.now(timezone.utc)
    limit = max(1, min(int(limit), 900))
    result = {}
    resets = list(db.scalars(select(PasswordReset.user_id).where(PasswordReset.expires_at <= now).limit(limit)))
    preferences = list(db.scalars(select(MailPreference).where(MailPreference.code_hash.is_not(None), MailPreference.code_until <= now).limit(limit)))
    bodies = list(db.scalars(select(MailDelivery).where((MailDelivery.body != '') | (MailDelivery.body_ciphertext.is_not(None) & MailDelivery.status.not_in((*QUEUED_STATUSES, 'sending'))), MailDelivery.status!='sending').order_by(MailDelivery.id).limit(limit)))
    codes = list(db.scalars(select(RedemptionCode).where(RedemptionCode.code.is_not(None)).order_by(RedemptionCode.id).limit(limit)))
    result.update(expired_resets=len(resets), expired_codes=len(preferences), mail_payloads=len(bodies), legacy_redemption_codes=len(codes))
    if apply:
        if resets:
            db.execute(delete(PasswordReset).where(PasswordReset.user_id.in_(resets), PasswordReset.expires_at <= now))
        for pref in preferences:
            db.execute(update(MailPreference).where(MailPreference.user_id==pref.user_id, MailPreference.code_until <= now).values(code_hash=None, code_until=None, attempts=0))
        for row in bodies:
            if row.status in QUEUED_STATUSES:
                sealed = cipher.encrypt_for(row.body.encode(), 'mail-body', _context(row))
                values = dict(body='', body_ciphertext=sealed.ciphertext, body_nonce=sealed.nonce, status='queued_secure')
            else:
                values = dict(body='', body_ciphertext=None, body_nonce=None)
            db.execute(update(MailDelivery).where(MailDelivery.id==row.id, MailDelivery.status==row.status, MailDelivery.body==row.body).values(**values).execution_options(synchronize_session=False))
        for code in codes:
            sealed = cipher.encrypt_for(code.code.encode(), 'redemption-code', (code.id + ':' + code.code_hash).encode())
            code.code_ciphertext, code.code_nonce, code.code = sealed.ciphertext, sealed.nonce, None
    return {'apply': apply, **result}
