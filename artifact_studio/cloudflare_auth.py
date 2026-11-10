"""Cloudflare Access JWT verification against the configured team's fixed JWKS."""

import asyncio
import re
import time
from typing import cast

import httpx
import jwt
from jwt import PyJWK
from starlette.datastructures import Headers

from .authentication import AuthenticationError, AuthenticationUnavailable, Principal


class CloudflareAuthProvider:
    def __init__(self, options: dict[str, object]) -> None:
        domain, audience = options.get("team_domain"), options.get("audience")
        if (
            set(options) != {"team_domain", "audience"}
            or not isinstance(domain, str)
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com", domain)
            or not isinstance(audience, str)
            or not audience
        ):
            raise ValueError("Invalid Cloudflare Access configuration")
        self.issuer = f"https://{domain}"
        self.audience = audience
        self._keys: dict[str, PyJWK] = {}
        self._expires = 0.0
        self._last_refresh = 0.0
        self._failed_until = 0.0
        self._lock = asyncio.Lock()

    async def _key(self, kid: str) -> PyJWK:
        async with self._lock:
            now = time.monotonic()
            if now >= self._expires and now < self._failed_until:
                raise AuthenticationUnavailable()
            if now >= self._expires or (kid not in self._keys and now - self._last_refresh >= 30):
                self._last_refresh = now
                try:
                    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:  # noqa: SIM117
                        async with client.stream(
                            "GET", f"{self.issuer}/cdn-cgi/access/certs"
                        ) as response:
                            response.raise_for_status()
                            body = b""
                            async for chunk in response.aiter_bytes():
                                body += chunk
                                if len(body) > 262144:
                                    raise ValueError("JWKS too large")
                    data: object = __import__("json").loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("Invalid JWKS")
                    data = cast(dict[str, object], data)
                    raw_keys_obj = data.get("keys")
                    if not isinstance(raw_keys_obj, list):
                        raise ValueError("Invalid JWKS")
                    raw_keys = cast(list[object], raw_keys_obj)
                    if len(raw_keys) > 64:
                        raise ValueError("Invalid JWKS")
                    keys: dict[str, PyJWK] = {}
                    for value in raw_keys:
                        if not isinstance(value, dict):
                            continue
                        raw = cast(dict[str, object], value)
                        raw_kid = raw.get("kid")
                        if raw.get("kty") == "RSA" and isinstance(raw_kid, str):
                            key = PyJWK.from_dict(raw, algorithm="RS256")
                            if raw_kid in keys:
                                raise ValueError("Duplicate JWKS key")
                            keys[raw_kid] = key
                    if not keys:
                        raise ValueError("Empty JWKS")
                    self._keys = keys
                    self._expires = now + 300
                    self._failed_until = 0.0
                except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
                    self._failed_until = now + 30
                    raise AuthenticationUnavailable() from exc
            key = self._keys.get(kid)
            if key is None:
                raise AuthenticationError()
            return key

    async def authenticate(self, headers: Headers) -> Principal:
        access = headers.getlist("cf-access-jwt-assertion")
        authorization = headers.getlist("authorization")
        if len(access) + len(authorization) != 1:
            raise AuthenticationError()
        if authorization:
            scheme, separator, token = authorization[0].partition(" ")
            if scheme.lower() != "bearer" or not separator or not token or " " in token:
                raise AuthenticationError()
        else:
            token = access[0]
        if len(token) > 16384:
            raise AuthenticationError()
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (
                header.get("alg") != "RS256"
                or not isinstance(kid, str)
                or not kid
                or len(kid) > 256
            ):
                raise AuthenticationError()
            key = await self._key(kid)
            claims = cast(
                dict[str, object],
                jwt.decode(
                    token,
                    key.key,
                    algorithms=["RS256"],
                    audience=self.audience,
                    issuer=self.issuer,
                    options={"require": ["sub", "exp", "iat", "aud", "iss"]},
                ),
            )
            subject, email, exp, iat = (
                claims["sub"],
                claims.get("email"),
                claims["exp"],
                claims["iat"],
            )
            if (
                not isinstance(subject, str)
                or (email is not None and not isinstance(email, str))
                or isinstance(exp, bool)
                or isinstance(iat, bool)
                or not isinstance(exp, (int, float))
                or not isinstance(iat, (int, float))
                or not 0 < exp - iat <= 86400
            ):
                raise AuthenticationError()
            if not subject:
                common_name = claims.get("common_name")
                if (
                    email
                    or claims.get("type") != "app"
                    or not isinstance(common_name, str)
                    or not common_name
                ):
                    raise AuthenticationError()
                return Principal(common_name, service=True)
            if not email:
                raise AuthenticationError()
            return Principal(subject, email.lower())
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise AuthenticationError() from exc
