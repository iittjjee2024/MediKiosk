"""Authentication primitives.

Passwords use PBKDF2-HMAC-SHA256 from the standard library rather than bcrypt:
it is equally sound for this purpose and avoids a native build step, which
matters when the deployment target is a government-cloud image built offline.

Tokens are short-lived by design. A kiosk is a shared device, so a patient
token is bound to the device and expires on inactivity -- no credential may
outlive the patient's session on hardware the next patient will touch.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from ..config import settings

PBKDF2_ROUNDS = 200_000
_ALG = "HS256"

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, want = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), want)

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class TokenError(ValueError):
    pass


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    tenant_id: str
    kind: str
    role: str | None = None
    session_id: str | None = None
    device_id: str | None = None
    department: str | None = None


def issue_token(claims: TokenClaims, *, minutes: int | None = None) -> str:
    ttl = minutes or (settings.patient_token_minutes
                      if claims.kind == "patient"
                      else settings.staff_token_minutes)
    now = int(time.time())
    payload = {
        "sub": claims.sub,
        "tid": claims.tenant_id,
        "knd": claims.kind,
        "rol": claims.role,
        "sid": claims.session_id,
        "did": claims.device_id,
        "dep": claims.department,
        "iat": now,
        "exp": now + ttl * 60,
    }
    header = _b64u(json.dumps({"alg": _ALG, "typ": "JWT"},
                              separators=(",", ":")).encode())
    body = _b64u(json.dumps(payload, separators=(",", ":"),
                            sort_keys=True).encode())
    signing_input = f"{header}.{body}".encode()
    sig = hmac.new(settings.jwt_secret.encode(), signing_input,
                   hashlib.sha256).digest()
    return f"{header}.{body}.{_b64u(sig)}"


def decode_token(token: str) -> TokenClaims:
    try:
        header_b64, body_b64, sig_b64 = token.split(".")
    except ValueError:
        raise TokenError("malformed token")

    signing_input = f"{header_b64}.{body_b64}".encode()
    expected = hmac.new(settings.jwt_secret.encode(), signing_input,
                        hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64u_decode(sig_b64)):
        raise TokenError("bad signature")

    payload = json.loads(_b64u_decode(body_b64))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise TokenError("token expired")

    return TokenClaims(
        sub=payload["sub"], tenant_id=payload["tid"], kind=payload["knd"],
        role=payload.get("rol"), session_id=payload.get("sid"),
        device_id=payload.get("did"), department=payload.get("dep"),
    )
