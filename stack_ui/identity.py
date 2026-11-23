"""Verification of the Komuta access identity JWT (``x-komuta-identity``).

Komuta's access protection forwards every signed-in visitor's identity as an ES256 JWT.
Rules (design §13): alg must be ES256, ``kid`` must be in the published key set, ``iss`` is
``komuta-access``, ``aud`` is the service's own host, ``exp``/``nbf`` valid, ``sid`` and ``sub``
present. The convenience headers ``x-komuta-user-id`` / ``x-komuta-user-email`` are never trusted.

Requires the optional ``cryptography`` package (``pip install komuta-stack-ui``).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("stack_ui")

try:  # optional dependency
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    HAVE_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - exercised by monkeypatching HAVE_CRYPTOGRAPHY
    HAVE_CRYPTOGRAPHY = False

DEFAULT_KEYS_URL = "https://api.komuta.io/api/devopszon/access-protection/identity-keys"
ISSUER = "komuta-access"
MAX_LIFETIME = 900  # seconds; Komuta issues 5-minute tokens, anything far longer is rejected
MAX_REMAINING_WITHOUT_IAT = 600  # seconds; a token without iat may not be valid for longer than this from now
MAX_TOKEN_BYTES = 8192


class IdentityError(Exception):
    """The identity token is missing or invalid. ``reason`` is safe to log (never the token)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class KomutaIdentity:
    sub: str
    email: str | None
    sid: str
    exp: int
    claims: dict


def _b64url_decode(part: str) -> bytes:
    if not isinstance(part, str) or not part or len(part) > MAX_TOKEN_BYTES:
        raise IdentityError("malformed token")
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (ValueError, TypeError):
        raise IdentityError("malformed token") from None


def _json(part: str) -> dict:
    try:
        value = json.loads(_b64url_decode(part))
    except (ValueError, RecursionError):
        raise IdentityError("malformed token") from None
    if not isinstance(value, dict):
        raise IdentityError("malformed token")
    return value


class KeySet:
    """Cached JWKS from ``url``. Unknown ``kid`` triggers at most one refresh per ``min_refresh`` seconds."""

    def __init__(self, url: str = DEFAULT_KEYS_URL, *, ttl: float = 300.0, min_refresh: float = 30.0,
                 timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic):
        self.url = url
        self.ttl = ttl
        self.min_refresh = min_refresh
        self.timeout = timeout
        self.transport = transport
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._last_attempt = -1e9
        self.fetches = 0

    async def _fetch(self) -> None:
        self._last_attempt = self._clock()
        self.fetches += 1
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.get(self.url, headers={"Accept": "application/json"})
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not fetch Komuta identity keys: %s", type(exc).__name__)
            return
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            log.warning("Komuta identity keys: unexpected response (HTTP %s)", resp.status_code)
            return
        keys: dict[str, Any] = {}
        for jwk in data["keys"]:
            key = _load_jwk(jwk)
            if key is not None:
                keys[jwk["kid"]] = key
        self._keys = keys
        self._fetched_at = self._clock()

    async def get(self, kid: str) -> Any | None:
        now = self._clock()
        if not self._keys or now - self._fetched_at > self.ttl:
            await self._fetch()  # on failure the stale keys (if any) stay usable
        key = self._keys.get(kid)
        if key is None and self._clock() - self._last_attempt >= self.min_refresh:
            await self._fetch()  # key rotation: refresh once for an unknown kid
            key = self._keys.get(kid)
        return key


def _load_jwk(jwk: Any) -> Any | None:
    if not isinstance(jwk, dict) or not isinstance(jwk.get("kid"), str):
        return None
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return None
    if jwk.get("alg") not in (None, "ES256") or jwk.get("use") not in (None, "sig"):
        return None
    try:
        x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
        y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    except (KeyError, ValueError, IdentityError):
        return None


async def verify_identity(token: str | None, *, keys: KeySet, audiences: set[str], leeway: int = 60,
                          now: float | None = None) -> KomutaIdentity:
    """Verify a Komuta identity JWT. Raises :class:`IdentityError` on any problem."""
    if not HAVE_CRYPTOGRAPHY:
        raise IdentityError("cryptography package is not installed")
    if not token:
        raise IdentityError("missing x-komuta-identity")
    token = token.strip()
    if len(token) > MAX_TOKEN_BYTES or token.count(".") != 2:
        raise IdentityError("malformed token")
    h64, p64, s64 = token.split(".")
    header = _json(h64)
    if header.get("alg") != "ES256":  # rejects none, HS256, RS256 ...
        raise IdentityError("alg must be ES256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise IdentityError("kid missing")
    signature = _b64url_decode(s64)
    if len(signature) != 64:
        raise IdentityError("bad signature")
    key = await keys.get(kid)
    if key is None:
        raise IdentityError("unknown kid")
    der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
    try:
        key.verify(der, f"{h64}.{p64}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise IdentityError("bad signature") from None
    claims = _json(p64)
    current = time.time() if now is None else now
    if claims.get("iss") != ISSUER:
        raise IdentityError("wrong issuer")
    aud = claims.get("aud")
    aud_values = {aud} if isinstance(aud, str) else set(aud) if isinstance(aud, list) else set()
    if not audiences or not {a.lower() for a in aud_values if isinstance(a, str)} & audiences:
        raise IdentityError("wrong audience")
    exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
    if not isinstance(exp, (int, float)) or current > exp + leeway:
        raise IdentityError("expired")
    if nbf is not None and (not isinstance(nbf, (int, float)) or current < nbf - leeway):
        raise IdentityError("not yet valid")
    start = iat if isinstance(iat, (int, float)) else nbf
    if isinstance(start, (int, float)) and exp - start > MAX_LIFETIME:
        raise IdentityError("lifetime too long")
    if not isinstance(iat, (int, float)) and exp - current > MAX_REMAINING_WITHOUT_IAT + leeway:
        raise IdentityError("lifetime too long")  # no iat: the remaining validity itself must be short
    sid, sub = claims.get("sid"), claims.get("sub")
    if not isinstance(sid, str) or not sid:
        raise IdentityError("sid missing")
    if not isinstance(sub, str) or not sub:
        raise IdentityError("sub missing")
    email = claims.get("email")
    return KomutaIdentity(sub=sub, email=email if isinstance(email, str) and email else None, sid=sid,
                          exp=int(exp), claims=claims)
