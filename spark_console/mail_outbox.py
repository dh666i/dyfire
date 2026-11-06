"""Encrypted outbox payloads with row binding and backward-safe queue states."""
import json

QUEUED_STATUSES=('queued','queued_secure')


def _context(row):
    return json.dumps([row.id,row.user_id,row.recipient,row.event,row.subject],ensure_ascii=False,separators=(',',':')).encode()


def seal_body(row,cipher,*,queued=True):
    if row.body_ciphertext is None:
        from spark_console.models import uuid_string
        if not row.id:row.id=uuid_string()
        sealed=cipher.encrypt_for((row.body or '').encode(),'mail-body',_context(row))
        row.body_ciphertext,row.body_nonce=sealed.ciphertext,sealed.nonce
        row.body=''
    if queued:
        # Legacy consumers ignore this new pending state instead of sending
        # empty/ciphertext bodies after a rollback.
        row.status='queued_secure'
    return row


def open_body(row,cipher):
    if row.body_ciphertext is None:return row.body or ''
    return cipher.decrypt_for(row.body_ciphertext,row.body_nonce,'mail-body',_context(row)).decode()


def erase_body(row):
    row.body='';row.body_ciphertext=None;row.body_nonce=None


def public_status(status):
    return 'queued' if status=='queued_secure' else status
