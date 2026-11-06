from __future__ import annotations

import os
import hmac
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext: bytes
    nonce: bytes


class CookieCipher:
    _AAD = b"douyin-cookie-v1"

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        self._aes = AESGCM(key)
        self._mac_key = hmac.new(key,b'dyfire-verification-digest-v2',hashlib.sha256).digest()

    def encrypt(self, payload: bytes) -> EncryptedPayload:
        nonce = os.urandom(12)
        return EncryptedPayload(
            ciphertext=self._aes.encrypt(nonce, payload, self._AAD), nonce=nonce
        )

    def decrypt(self, ciphertext: bytes, nonce: bytes) -> bytes:
        return self._aes.decrypt(nonce, ciphertext, self._AAD)

    def digest(self, purpose: str, value: str) -> str:
        return hmac.new(self._mac_key,purpose.encode()+b'\0'+value.encode(),hashlib.sha256).hexdigest()

    def encrypt_for(self, payload: bytes, purpose: str, context: bytes = b'') -> EncryptedPayload:
        nonce=os.urandom(12)
        aad=b'dyfire-purpose-v1\0'+purpose.encode()+b'\0'+context
        return EncryptedPayload(self._aes.encrypt(nonce,payload,aad),nonce)

    def decrypt_for(self, ciphertext: bytes, nonce: bytes, purpose: str, context: bytes = b'') -> bytes:
        return self._aes.decrypt(nonce,ciphertext,b'dyfire-purpose-v1\0'+purpose.encode()+b'\0'+context)
