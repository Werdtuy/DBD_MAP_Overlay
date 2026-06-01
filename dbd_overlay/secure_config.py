from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os


CONFIG_VERSION = 1
CONFIG_KEY = bytes.fromhex("e8a3567ff8015062f84bd21f0fa6b4bef910c2ff1f638805c01dea7dcc43042b")


def encrypt_server_url(server_url: str) -> str:
    normalized = _normalize_url(server_url)
    plaintext = json.dumps({"server_url": normalized}, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(16)
    ciphertext = _xor(plaintext, _keystream(nonce, len(plaintext)))
    signature = hmac.new(CONFIG_KEY, b"dbd-overlay-config-v1" + nonce + ciphertext, hashlib.sha256).digest()
    return json.dumps(
        {
            "version": CONFIG_VERSION,
            "nonce": _encode(nonce),
            "ciphertext": _encode(ciphertext),
            "signature": _encode(signature),
        },
        indent=2,
    )


def decrypt_server_url(encrypted_config: str) -> str:
    try:
        payload = json.loads(encrypted_config)
        if int(payload["version"]) != CONFIG_VERSION:
            raise ValueError("Unsupported encrypted configuration version")
        nonce = _decode(payload["nonce"])
        ciphertext = _decode(payload["ciphertext"])
        signature = _decode(payload["signature"])
    except Exception as exc:
        raise ValueError("Encrypted activation configuration is invalid") from exc
    expected = hmac.new(CONFIG_KEY, b"dbd-overlay-config-v1" + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Encrypted activation configuration signature is invalid")
    plaintext = _xor(ciphertext, _keystream(nonce, len(ciphertext)))
    try:
        return _normalize_url(str(json.loads(plaintext.decode("utf-8"))["server_url"]))
    except Exception as exc:
        raise ValueError("Encrypted activation configuration payload is invalid") from exc


def _normalize_url(server_url: str) -> str:
    normalized = server_url.strip().rstrip("/")
    if not normalized.startswith("https://"):
        raise ValueError("Activation service URL must use HTTPS")
    return normalized


def _keystream(nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hmac.new(CONFIG_KEY, b"stream" + nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(output[:length])


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
