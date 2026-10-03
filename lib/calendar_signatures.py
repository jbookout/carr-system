"""Ed25519 calendar envelopes stay in memory on every supported platform."""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _private_key(pem: bytes) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except UnsupportedAlgorithm as exc:
        raise ValueError("calendar signing key must be Ed25519") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("calendar signing key must be Ed25519")
    return key


def public_key_pem(private_pem: bytes) -> bytes:
    return _private_key(private_pem).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def sign(private_pem: bytes, payload: bytes) -> bytes:
    return _private_key(private_pem).sign(payload)


def verify(public_pem: bytes, payload: bytes, signature: bytes) -> bool:
    try:
        key = serialization.load_pem_public_key(public_pem)
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(signature, payload)
    except (InvalidSignature, UnsupportedAlgorithm, TypeError, ValueError):
        return False
    return True
