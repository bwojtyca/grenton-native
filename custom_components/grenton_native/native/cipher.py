"""AES-128-CBC / PKCS7 cipher for the Grenton native command channel.

The CLU command protocol encrypts every message with a single static key + IV
(both 16 bytes). That static-IV reuse is the protocol's design, not ours — we
mirror it so we can interoperate.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# Factory-default key/IV a CLU uses before a project assigns its own. These are
# public constants (documented by the OpenGr8on project) and grant nothing beyond
# talking to an unprovisioned / default-keyed CLU.
DEFAULT_KEY_B64 = "hd5SHpxl0N5+WEXTXlPQmw=="
DEFAULT_IV_B64 = "ua/jh/kZo9Og15rejhGhFg=="

_AES_BLOCK_BITS = 128


class GrentonCipher:
    """Encrypt/decrypt a single CLU message.

    Key must be 16/24/32 bytes (AES-128/192/256; Grenton uses 128). IV is 16 bytes.
    ``decrypt`` returns ``None`` for anything that is not valid ciphertext for this
    key (wrong length or bad padding) instead of raising, so a stray datagram never
    crashes the receive loop.
    """

    __slots__ = ("_key", "_iv")

    def __init__(self, key: bytes, iv: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError(f"AES key must be 16/24/32 bytes, got {len(key)}")
        if len(iv) != 16:
            raise ValueError(f"AES IV must be 16 bytes, got {len(iv)}")
        self._key = key
        self._iv = iv

    @classmethod
    def from_base64(cls, key_b64: str, iv_b64: str) -> GrentonCipher:
        return cls(base64.b64decode(key_b64), base64.b64decode(iv_b64))

    @classmethod
    def default(cls) -> GrentonCipher:
        """Cipher using the public factory-default key/IV."""
        return cls.from_base64(DEFAULT_KEY_B64, DEFAULT_IV_B64)

    def encrypt(self, plaintext: bytes) -> bytes:
        padder = PKCS7(_AES_BLOCK_BITS).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, ciphertext: bytes) -> bytes | None:
        if not ciphertext or len(ciphertext) % 16 != 0:
            return None
        try:
            decryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = PKCS7(_AES_BLOCK_BITS).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            # Bad PKCS7 padding → not a message encrypted with our key.
            return None
