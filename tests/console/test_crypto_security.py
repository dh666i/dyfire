import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException

from spark_console.crypto import CookieCipher
from spark_console.security import CsrfService, PasswordService, SessionService
from spark_console.web.auth import WebAuth


class CookieCipherTests(unittest.TestCase):
    def test_round_trip_and_tamper_detection(self):
        cipher = CookieCipher(b"k" * 32)
        sealed = cipher.encrypt(b'[{"name":"sessionid","value":"secret"}]')
        self.assertEqual(
            b'[{"name":"sessionid","value":"secret"}]',
            cipher.decrypt(sealed.ciphertext, sealed.nonce),
        )
        tampered = sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1])
        with self.assertRaises(InvalidTag):
            cipher.decrypt(tampered, sealed.nonce)

    def test_rejects_non_256_bit_key(self):
        with self.assertRaisesRegex(ValueError, "32-byte"):
            CookieCipher(b"short")


class PasswordTests(unittest.TestCase):
    def test_hash_and_verify_without_storing_plaintext(self):
        service = PasswordService()
        encoded = service.hash("Temp-1234-long")
        self.assertNotIn("Temp-1234-long", encoded)
        self.assertTrue(service.verify(encoded, "Temp-1234-long"))
        self.assertFalse(service.verify(encoded, "wrong"))

    def test_hash_accepts_short_nonempty_passwords(self):
        service = PasswordService()
        encoded = service.hash("A1")

        self.assertTrue(service.verify(encoded, "A1"))
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            service.hash("")


class SessionTests(unittest.TestCase):
    def test_database_record_stores_only_session_token_hash(self):
        service = SessionService(session_key=b"s" * 32)
        raw, record = service.create_record(
            "user-id", now=datetime(2026, 8, 25, tzinfo=timezone.utc)
        )
        self.assertNotEqual(raw, record.token_hash)
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), record.token_hash)
        self.assertEqual(
            datetime(2026, 8, 25, tzinfo=timezone.utc) + timedelta(hours=8),
            record.expires_at,
        )
        self.assertTrue(service.matches(raw, record))

    def test_csrf_compare_is_constant_time_boundary(self):
        csrf = CsrfService()
        token = csrf.issue()
        self.assertTrue(csrf.verify(token, token))
        self.assertFalse(csrf.verify(token, token + "x"))

    def test_web_csrf_rejects_non_string_values_as_forbidden(self):
        auth = WebAuth(SessionService(session_key=b"s" * 32))
        record = SimpleNamespace(csrf_token="csrf")
        for supplied in (None, 1, {}, []):
            with self.subTest(supplied=supplied):
                with self.assertRaises(HTTPException) as error:
                    auth.csrf(record, supplied)
                self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
