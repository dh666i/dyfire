"""Create a re-encrypted SQLite copy. Never replace live data or either key."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from spark_console.crypto import CookieCipher
from spark_console.mail_outbox import _context


def rotate_copy(database,old_key,new_key,output):
    source=Path(database).resolve(strict=True);destination=Path(output).resolve()
    old,new=CookieCipher(old_key),CookieCipher(new_key)
    if old_key==new_key:raise ValueError('new key must differ from old key')
    destination.mkdir(mode=0o700,parents=False,exist_ok=False)
    copied=destination/'spark.db';target=None;counts={}
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original:
            target=sqlite3.connect(copied);original.backup(target)
        target.row_factory=sqlite3.Row;target.execute('PRAGMA foreign_keys=ON')
        target.execute('BEGIN IMMEDIATE')
        if target.execute("SELECT count(*) FROM task_runs WHERE status='running'").fetchone()[0]:
            raise ValueError('stop writers and finish running tasks before preparing a rotation copy')
        for table,key,encrypted,nonce in (
            ('douyin_accounts','id','encrypted_cookies','cookie_nonce'),
            ('mail_settings','id','ciphertext','nonce'),
            ('invite_code_secrets','invite_id','ciphertext','nonce'),
        ):
            counts[table]=0
            for row in target.execute(f'SELECT {key},{encrypted},{nonce} FROM {table}').fetchall():
                if not row[encrypted]:continue
                plaintext=bytearray(old.decrypt(row[encrypted],row[nonce]))
                try:
                    sealed=new.encrypt(bytes(plaintext))
                    assert new.decrypt(sealed.ciphertext,sealed.nonce)==bytes(plaintext)
                finally:plaintext[:]=b'\0'*len(plaintext);plaintext.clear()
                target.execute(f'UPDATE {table} SET {encrypted}=?,{nonce}=? WHERE {key}=?',(sealed.ciphertext,sealed.nonce,row[key]))
                counts[table]+=1
        counts['redemption_codes']=0
        for row in target.execute('SELECT * FROM redemption_codes WHERE code_ciphertext IS NOT NULL OR code IS NOT NULL').fetchall():
            context=(row['id']+':'+row['code_hash']).encode()
            raw=row['code'].encode() if row['code'] is not None else old.decrypt_for(row['code_ciphertext'],row['code_nonce'],'redemption-code',context)
            plaintext=bytearray(raw);del raw
            try:
                sealed=new.encrypt_for(bytes(plaintext),'redemption-code',context)
                assert new.decrypt_for(sealed.ciphertext,sealed.nonce,'redemption-code',context)==bytes(plaintext)
            finally:plaintext[:]=b'\0'*len(plaintext);plaintext.clear()
            target.execute('UPDATE redemption_codes SET code=NULL,code_ciphertext=?,code_nonce=? WHERE id=?',(sealed.ciphertext,sealed.nonce,row['id']))
            counts['redemption_codes']+=1
        counts['mail_deliveries']=0
        for row in target.execute('SELECT * FROM mail_deliveries WHERE body_ciphertext IS NOT NULL').fetchall():
            context=_context(SimpleNamespace(**dict(row)))
            plaintext=bytearray(old.decrypt_for(row['body_ciphertext'],row['body_nonce'],'mail-body',context))
            try:sealed=new.encrypt_for(bytes(plaintext),'mail-body',context)
            finally:plaintext[:]=b'\0'*len(plaintext);plaintext.clear()
            target.execute('UPDATE mail_deliveries SET body_ciphertext=?,body_nonce=? WHERE id=?',(sealed.ciphertext,sealed.nonce,row['id']))
            counts['mail_deliveries']+=1
        # Keyed code digests cannot be recomputed without the original code.
        # Expire them and their outgoing emails instead of sending unusable codes.
        target.execute('DELETE FROM password_resets')
        target.execute('UPDATE mail_preferences SET code_hash=NULL,code_until=NULL,attempts=0')
        target.execute("UPDATE mail_deliveries SET status='cancelled',body='',body_ciphertext=NULL,body_nonce=NULL WHERE event IN ('reset','verify') AND status IN ('queued','queued_secure','sending')")
        target.execute("UPDATE douyin_login_sessions SET status='cancelled',slot=NULL,qr_png=NULL,error_code='cancelled' WHERE status NOT IN ('succeeded','failed','expired','cancelled')")
        target.execute("UPDATE douyin_login_inputs SET ciphertext=NULL,nonce=NULL,delivery_status='failed' WHERE ciphertext IS NOT NULL OR delivery_status IN ('queued','claimed')")
        target.execute("UPDATE douyin_login_actions SET payload_json=NULL,delivery_status='failed' WHERE delivery_status IN ('queued','processing')")
        target.execute('DELETE FROM account_operations')
        target.execute("UPDATE task_probes SET status='failed',result_json='{\"summary\":\"密钥维护后请重新检查\"}' WHERE status IN ('queued','running')")
        if target.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign key validation failed')
        target.commit()
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('integrity validation failed')
        target.close();target=None
        copied.chmod(0o600)
        report={'schema':1,'new_key_id':hashlib.sha256(new_key).hexdigest()[:16],
            'counts':counts,'sha256':hashlib.sha256(copied.read_bytes()).hexdigest(),
            'source_changed':False,'installed':False,'one_time_codes_expired':True}
        (destination/'rotation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        return report
    except BaseException:
        if target is not None:target.close()
        for name in ('spark.db','spark.db-wal','spark.db-shm','spark.db-journal','rotation.json'):
            (destination/name).unlink(missing_ok=True)
        destination.rmdir()
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--old-key',type=Path,required=True)
    parser.add_argument('--new-key',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:report=rotate_copy(args.database,args.old_key.read_bytes(),args.new_key.read_bytes(),args.output)
    except Exception as error:raise SystemExit('Rotation copy failed: '+type(error).__name__) from None
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
