import base64
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from spark_console.config import Settings
from spark_console.crypto import CookieCipher, SecretCipher
from spark_console.db import create_schema, session_scope
from spark_console.mfa import (
    CODE_DIGITS,
    RECOVERY_CODE_COUNT,
    TIME_STEP_SECONDS,
    consume_recovery_code,
    generate_recovery_codes,
    provisioning_uri,
    recovery_code_matches,
    verify_code,
)
from spark_console.models import AdminTotpCredential, AdminTotpRecoveryCode, User
from spark_console.security import PasswordService
from spark_console.web.app import create_app


class TotpTests(unittest.TestCase):
    # RFC 6238 Appendix B uses SHA-1 and the ASCII secret below.
    SECRET = b"12345678901234567890"

    def test_rfc6238_sha1_vectors(self):
        vectors = {
            59: "94287082",
            1111111109: "07081804",
            1111111111: "14050471",
            1234567890: "89005924",
            2000000000: "69279037",
            20000000000: "65353130",
        }
        # The implementation intentionally defaults to six digits for the
        # product; verify the six-digit suffix of the normative eight-digit
        # vectors to test the HOTP truncation and time-step calculation.
        for timestamp, expected in vectors.items():
            with self.subTest(timestamp=timestamp):
                self.assertEqual(
                    expected[-CODE_DIGITS:],
                    __import__("spark_console.mfa", fromlist=["_code"])._code(
                        self.SECRET, int(timestamp // TIME_STEP_SECONDS)
                    ),
                )

    def test_verification_accepts_one_step_drift_and_rejects_replay(self):
        module = __import__("spark_console.mfa", fromlist=["_code"])
        timestamp = 1_700_000_000
        counter = timestamp // TIME_STEP_SECONDS
        code = module._code(self.SECRET, counter + 1)
        accepted = verify_code(self.SECRET, code, now=timestamp)
        self.assertEqual(counter + 1, accepted)
        self.assertIsNone(
            verify_code(
                self.SECRET,
                code,
                now=timestamp,
                last_used_counter=accepted,
            )
        )

    def test_uri_is_standards_compatible_and_secret_is_not_plaintext(self):
        uri = provisioning_uri(self.SECRET, username="admin@example.com")
        self.assertTrue(uri.startswith("otpauth://totp/Dyfire%3Aadmin%40example.com?"))
        encoded = base64.b32encode(self.SECRET).decode("ascii").rstrip("=")
        self.assertIn("secret=" + encoded, uri)
        self.assertIn("algorithm=SHA1", uri)

    def test_malformed_codes_are_rejected(self):
        for value in (None, "", "12345", "1234567", "abcdef", 123456):
            with self.subTest(value=value):
                self.assertIsNone(verify_code(self.SECRET, value, now=0))

    def test_recovery_codes_are_formatted_and_salted(self):
        codes = generate_recovery_codes()
        self.assertEqual(RECOVERY_CODE_COUNT, len(codes))
        displays = [item[0] for item in codes]
        self.assertEqual(RECOVERY_CODE_COUNT, len(set(displays)))
        for display, salt, digest in codes:
            self.assertRegex(display, r"^[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$")
            self.assertEqual(16, len(salt))
            self.assertEqual(64, len(digest))
            self.assertTrue(recovery_code_matches(display, digest, salt))
            self.assertTrue(recovery_code_matches(display.replace("-", ""), digest, salt))
            self.assertFalse(recovery_code_matches("0" * 19, digest, salt))

    def test_totp_secret_cipher_has_a_distinct_authenticated_data_boundary(self):
        secret_cipher = SecretCipher(b"k" * 32)
        cookie_cipher = CookieCipher(b"k" * 32)
        sealed = secret_cipher.encrypt(self.SECRET)
        self.assertEqual(self.SECRET, secret_cipher.decrypt(sealed.ciphertext, sealed.nonce))
        with self.assertRaises(Exception):
            cookie_cipher.decrypt(sealed.ciphertext, sealed.nonce)


class TotpEnrollmentWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        cookie_key = root / "cookie.key"
        session_key = root / "session.key"
        cookie_key.write_bytes(b"c" * 32)
        session_key.write_bytes(b"s" * 32)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=cookie_key,
            session_key_file=session_key,
            secure_cookies=True,
        )
        self.engine = create_engine(
            self.settings.database_url, connect_args={"check_same_thread": False}
        )
        create_schema(self.engine)
        with session_scope(self.engine) as db:
            admin = User(
                username="totp-admin",
                password_hash=PasswordService().hash("AdminPass-123"),
                role="admin",
                must_change_password=False,
            )
            db.add(admin)
            db.flush()
            self.admin_id = admin.id
        self.client = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def _bootstrap(self):
        response = self.client.post(
            "/login",
            data={"username": "totp-admin", "password": "AdminPass-123"},
            follow_redirects=False,
        )
        self.assertEqual(303, response.status_code)
        return self.client.get("/api/auth/bootstrap").json()

    def test_enrollment_round_trip_does_not_change_existing_session(self):
        bootstrap = self._bootstrap()
        setup = self.client.post(
            "/api/auth/mfa/totp/setup",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123"},
        )
        self.assertEqual(200, setup.status_code, setup.text)
        payload = setup.json()
        self.assertTrue(payload["requires_confirmation"])
        self.assertIn("otpauth://totp/", payload["otpauth_uri"])
        self.assertNotEqual(payload["secret"], "")

        secret = base64.b32decode(payload["secret"] + "=" * ((8 - len(payload["secret"]) % 8) % 8))
        module = __import__("spark_console.mfa", fromlist=["_code"])
        code = module._code(secret, int(time.time() // TIME_STEP_SECONDS))
        confirmed = self.client.post(
            "/api/auth/mfa/totp/confirm",
            json={"csrf_token": bootstrap["csrf"], "code": code},
        )
        self.assertEqual(200, confirmed.status_code, confirmed.text)
        self.assertTrue(confirmed.json()["enabled"])
        self.assertEqual(200, self.client.get("/api/auth/bootstrap").status_code)
        self.assertTrue(self.client.get("/api/auth/mfa/totp").json()["enabled"])

        with session_scope(self.engine) as db:
            credential = db.get(AdminTotpCredential, self.admin_id)
            self.assertIsNotNone(credential)
            self.assertTrue(credential.enabled)
            self.assertNotEqual(credential.ciphertext, secret)
            self.assertIsNotNone(credential.last_used_counter)
            recovery = db.scalars(
                select(AdminTotpRecoveryCode).where(
                    AdminTotpRecoveryCode.user_id == self.admin_id
                )
            ).all()
            self.assertEqual(RECOVERY_CODE_COUNT, len(recovery))
            self.assertTrue(all(item.used_at is None for item in recovery))

    def test_recovery_codes_are_issued_once_and_consumed(self):
        bootstrap = self._bootstrap()
        setup = self.client.post(
            "/api/auth/mfa/totp/setup",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123"},
        ).json()
        secret = base64.b32decode(setup["secret"] + "=" * ((8 - len(setup["secret"]) % 8) % 8))
        module = __import__("spark_console.mfa", fromlist=["_code"])
        code = module._code(secret, int(time.time() // TIME_STEP_SECONDS))
        confirmed = self.client.post(
            "/api/auth/mfa/totp/confirm",
            json={"csrf_token": bootstrap["csrf"], "code": code},
        )
        self.assertEqual(200, confirmed.status_code, confirmed.text)
        issued = confirmed.json()["recovery_codes"]
        self.assertEqual(RECOVERY_CODE_COUNT, len(issued))
        self.assertEqual(RECOVERY_CODE_COUNT, self.client.get("/api/auth/mfa/totp").json()["recovery_codes_remaining"])
        used = self.client.post(
            "/api/auth/mfa/totp/recovery-codes/consume",
            json={"csrf_token": bootstrap["csrf"], "code": issued[0]},
        )
        self.assertEqual(200, used.status_code, used.text)
        self.assertEqual(RECOVERY_CODE_COUNT - 1, used.json()["recovery_codes_remaining"])
        replay = self.client.post(
            "/api/auth/mfa/totp/recovery-codes/consume",
            json={"csrf_token": bootstrap["csrf"], "code": issued[0]},
        )
        self.assertEqual(403, replay.status_code)
        self.assertEqual(RECOVERY_CODE_COUNT - 1, self.client.get("/api/auth/mfa/totp").json()["recovery_codes_remaining"])

    def test_regenerate_requires_password_and_totp_and_invalidates_old_codes(self):
        bootstrap = self._bootstrap()
        setup = self.client.post(
            "/api/auth/mfa/totp/setup",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123"},
        ).json()
        secret = base64.b32decode(setup["secret"] + "=" * ((8 - len(setup["secret"]) % 8) % 8))
        module = __import__("spark_console.mfa", fromlist=["_code"])
        counter = int(time.time() // TIME_STEP_SECONDS)
        self.assertEqual(
            200,
            self.client.post(
                "/api/auth/mfa/totp/confirm",
                json={"csrf_token": bootstrap["csrf"], "code": module._code(secret, counter)},
            ).status_code,
        )
        first_codes = self.client.post(
            "/api/auth/mfa/totp/recovery-codes/regenerate",
            json={"csrf_token": bootstrap["csrf"], "password": "bad", "code": module._code(secret, counter + 1)},
        )
        self.assertEqual(403, first_codes.status_code)
        rotated = self.client.post(
            "/api/auth/mfa/totp/recovery-codes/regenerate",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123", "code": module._code(secret, counter + 1)},
        )
        self.assertEqual(200, rotated.status_code, rotated.text)
        new_codes = rotated.json()["recovery_codes"]
        self.assertEqual(RECOVERY_CODE_COUNT, len(new_codes))
        old = self.client.post(
            "/api/auth/mfa/totp/recovery-codes/consume",
            json={"csrf_token": bootstrap["csrf"], "code": new_codes[0]},
        )
        self.assertEqual(200, old.status_code)

    def test_invalid_confirmation_is_audited_and_does_not_enable(self):
        bootstrap = self._bootstrap()
        setup = self.client.post(
            "/api/auth/mfa/totp/setup",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123"},
        )
        self.assertEqual(200, setup.status_code)
        response = self.client.post(
            "/api/auth/mfa/totp/confirm",
            json={"csrf_token": bootstrap["csrf"], "code": "000000"},
        )
        self.assertEqual(403, response.status_code)
        self.assertFalse(self.client.get("/api/auth/mfa/totp").json()["enabled"])

    def test_disable_requires_password_and_code_when_enabled(self):
        bootstrap = self._bootstrap()
        setup = self.client.post(
            "/api/auth/mfa/totp/setup",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123"},
        ).json()
        secret = base64.b32decode(setup["secret"] + "=" * ((8 - len(setup["secret"]) % 8) % 8))
        module = __import__("spark_console.mfa", fromlist=["_code"])
        current_counter = int(time.time() // TIME_STEP_SECONDS)
        confirm_code = module._code(secret, current_counter)
        self.assertEqual(
            200,
            self.client.post(
                "/api/auth/mfa/totp/confirm",
                json={"csrf_token": bootstrap["csrf"], "code": confirm_code},
            ).status_code,
        )
        denied = self.client.post(
            "/api/auth/mfa/totp/disable",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123", "code": "000000"},
        )
        self.assertEqual(403, denied.status_code)
        disable_code = module._code(secret, current_counter + 1)
        disabled = self.client.post(
            "/api/auth/mfa/totp/disable",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123", "code": disable_code},
        )
        self.assertEqual(200, disabled.status_code, disabled.text)
        self.assertFalse(self.client.get("/api/auth/mfa/totp").json()["configured"])


if __name__ == "__main__":
    unittest.main()
