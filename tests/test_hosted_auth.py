import asyncio
import json
import time
from collections.abc import Mapping
from importlib.metadata import EntryPoint, EntryPoints
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from artifact_studio.authentication import AuthenticationError, Principal, load_auth_provider
from artifact_studio.cloudflare_auth import CloudflareAuthProvider
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.hosted_auth import HostedAuth


def test_configuration_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTIFACT_STUDIO_MODE", "hosted")
    with pytest.raises(ValueError):
        DeploymentConfig.from_env()
    config = tmp_path / "hosted.json"
    config.write_text(json.dumps({"public_url": "https://a.example.com"}))
    monkeypatch.setenv("ARTIFACT_STUDIO_HOSTED_CONFIG", str(config))
    with pytest.raises(ValueError):
        DeploymentConfig.from_env()


class Provider:
    async def authenticate(self, headers: Headers) -> Principal:
        if headers.get("authorization") != "Bearer valid":
            raise AuthenticationError()
        return Principal("human", "member@example.com")


def create_fixture_provider(options: dict[str, object]) -> Provider:
    assert options == {"fixture": True}
    return Provider()


@pytest.mark.anyio
async def test_installed_provider_factory_and_unknown_provider_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def entries(*, group: str, name: str) -> EntryPoints:
        assert group == "artifact_studio.auth"
        return EntryPoints(
            [
                EntryPoint(
                    name="fixture",
                    value=__name__ + ":create_fixture_provider",
                    group=group,
                )
            ]
            if name == "fixture"
            else []
        )

    monkeypatch.setattr("artifact_studio.authentication.entry_points", entries)
    provider = load_auth_provider(
        DeploymentConfig(
            mode="hosted",
            auth_provider="fixture",
            auth_options={"fixture": True},
        )
    )
    assert (
        await provider.authenticate(Headers({"authorization": "Bearer valid"}))
    ).subject == "human"
    with pytest.raises(ValueError, match="Unknown authentication provider"):
        load_auth_provider(DeploymentConfig(mode="hosted", auth_provider="missing"))


def test_host_origin_membership_and_preview_boundary() -> None:
    app = FastAPI()

    @app.get("/api/data")
    async def data(request: Request) -> dict[str, str]:
        return {"subject": request.state.principal.subject}

    @app.post("/api/data")
    async def post() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_preview/token")
    async def preview() -> dict[str, bool]:
        return {"ok": True}

    config = DeploymentConfig(
        "hosted",
        "https://app.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        (),
    )
    client = TestClient(HostedAuth(app, config, Provider(), lambda _method, _path: True))
    assert client.get("https://app.example.com/api/data").status_code == 401
    assert (
        client.get(
            "https://evil.example.com/api/data", headers={"authorization": "Bearer valid"}
        ).status_code
        == 400
    )
    assert client.get("https://preview.example.com/api/data").status_code == 404
    assert client.get("https://preview.example.com/_preview/token").status_code == 200
    headers = {"authorization": "Bearer valid"}
    assert client.get("https://app.example.com/api/data", headers=headers).json() == {
        "subject": "human"
    }
    assert client.post("https://app.example.com/api/data", headers=headers).status_code == 403
    assert (
        client.post(
            "https://app.example.com/api/data",
            headers={**headers, "origin": "https://evil.example.com"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "https://app.example.com/api/data",
            headers={**headers, "origin": "https://app.example.com"},
        ).status_code
        == 200
    )


@pytest.mark.anyio
async def test_cloudflare_signed_claims() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = CloudflareAuthProvider(
        {"team_domain": "team.cloudflareaccess.com", "audience": "aud"}
    )
    provider._keys = {  # pyright: ignore[reportPrivateUsage]
        "key": jwt.PyJWK.from_dict(
            json.loads(RSAAlgorithm.to_jwk(key.public_key())) | {"kid": "key"},
            algorithm="RS256",
        )
    }
    provider._expires = time.monotonic() + 60  # pyright: ignore[reportPrivateUsage]
    now = int(time.time())
    claims = {
        "sub": "person",
        "email": "member@example.com",
        "iss": "https://team.cloudflareaccess.com",
        "aud": "aud",
        "iat": now,
        "exp": now + 60,
    }

    def headers(values: Mapping[str, object]) -> Headers:
        token = jwt.encode(dict(values), key, algorithm="RS256", headers={"kid": "key"})
        return Headers({"authorization": f"Bearer {token}"})

    assert (await provider.authenticate(headers(claims))).subject == "person"
    service_claims = claims | {
        "sub": "",
        "email": None,
        "common_name": "client.access",
        "type": "app",
    }
    service = await provider.authenticate(headers(service_claims))
    assert service == Principal("client.access", service=True)
    with pytest.raises(AuthenticationError):
        await provider.authenticate(headers(claims | {"aud": "wrong"}))
    for invalid in (
        {"iss": "https://evil.cloudflareaccess.com"},
        {"exp": now - 1},
        {"iat": now + 1000},
        {"nbf": now + 1000},
        {"sub": 123},
        {"sub": "", "email": None},
        {"exp": now + 999999},
    ):
        with pytest.raises(AuthenticationError):
            await provider.authenticate(headers(claims | invalid))
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims, forged, algorithm="RS256", headers={"kid": "key"})
    with pytest.raises(AuthenticationError):
        await provider.authenticate(Headers({"authorization": f"Bearer {token}"}))
    with pytest.raises(AuthenticationError):
        await provider.authenticate(Headers())
    with pytest.raises(AuthenticationError):
        await provider.authenticate(
            Headers(raw=[(b"authorization", b"Bearer x"), (b"cf-access-jwt-assertion", b"y")])
        )
    with pytest.raises(AuthenticationError):
        await provider.authenticate(
            Headers(raw=[(b"authorization", b"Bearer x"), (b"authorization", b"Bearer y")])
        )


def test_service_membership_and_forwarded_headers() -> None:
    app = FastAPI()

    @app.get("/mcp")
    async def mcp(request: Request) -> dict[str, object]:
        return {"url": str(request.url), "cookie": request.headers.get("cookie")}

    @app.get("/api/data")
    async def data() -> dict[str, bool]:
        return {"ok": True}

    class Service:
        async def authenticate(self, headers: Headers) -> Principal:
            return Principal("agent", service=True)

    config = DeploymentConfig(
        "hosted",
        "https://app.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        ("agent",),
    )
    client = TestClient(HostedAuth(app, config, Service(), lambda _method, _path: True))
    result = client.get(
        "https://app.example.com/mcp",
        headers={
            "x-forwarded-host": "evil.example.com",
            "x-forwarded-proto": "http",
            "cookie": "secret",
        },
    )
    assert result.json() == {"url": "https://app.example.com/mcp", "cookie": None}
    assert client.get("https://app.example.com/api/data").status_code == 403
    assert (
        client.get(
            "https://app.example.com/mcp", headers={"origin": "https://app.example.com"}
        ).status_code
        == 403
    )
    assert client.post("https://preview.example.com/_preview/token").status_code == 404
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("wss://app.example.com/events"),
    ):
        pass


def test_missing_credentials_cover_workspace_routes() -> None:
    app = FastAPI()
    config = DeploymentConfig(
        "hosted",
        "https://app.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        (),
    )
    client = TestClient(HostedAuth(app, config, Provider(), lambda _method, _path: True))
    for path in ("/", "/static/main.js", "/files/test", "/api/events", "/mcp"):
        assert client.get("https://app.example.com" + path).status_code == 401
    assert (
        client.get(
            "https://app.example.com/api/data",
            headers={"host": "evil.example.com", "x-forwarded-host": "app.example.com"},
        ).status_code
        == 400
    )


@pytest.mark.anyio
async def test_jwks_cache_rotation_and_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from artifact_studio.authentication import AuthenticationUnavailable

    first = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = {"one": first, "two": second}
    active = "one"
    calls = 0
    outage = False

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if outage:
            return httpx.Response(503)
        jwk = json.loads(RSAAlgorithm.to_jwk(keys[active].public_key()))
        return httpx.Response(200, json={"keys": [jwk | {"kid": active}]})

    original = httpx.AsyncClient

    def client(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    provider = CloudflareAuthProvider(
        {"team_domain": "team.cloudflareaccess.com", "audience": "aud"}
    )
    await provider._key("one")  # pyright: ignore[reportPrivateUsage]
    await provider._key("one")  # pyright: ignore[reportPrivateUsage]
    assert calls == 1
    with pytest.raises(AuthenticationError):
        await provider._key("two")  # pyright: ignore[reportPrivateUsage]
    assert calls == 1
    active = "two"
    provider._last_refresh = 0  # pyright: ignore[reportPrivateUsage]
    await provider._key("two")  # pyright: ignore[reportPrivateUsage]
    assert calls == 2
    outage = True
    provider._expires = 0  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(AuthenticationUnavailable):
        await provider._key("two")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(AuthenticationUnavailable):
        await provider._key("two")  # pyright: ignore[reportPrivateUsage]
    assert calls == 3


def test_mcp_body_limit_and_cache_policy() -> None:
    from fastapi.responses import Response

    app = FastAPI()

    @app.post("/mcp")
    async def mcp() -> Response:
        return Response("ok", headers={"Cache-Control": "public, max-age=3600"})

    config = DeploymentConfig(
        "hosted",
        "https://app.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        (),
    )
    client = TestClient(HostedAuth(app, config, Provider(), lambda _method, _path: True))
    headers = {"authorization": "Bearer valid"}
    response = client.post("https://app.example.com/mcp", headers=headers, content=b"x")
    assert response.status_code == 200
    assert response.headers.get_list("cache-control") == ["no-store"]
    assert (
        client.post(
            "https://app.example.com/mcp", headers=headers, content=b"x" * (2 * 1024 * 1024 + 1)
        ).status_code
        == 413
    )


def test_mcp_timeout_returns_response() -> None:
    import asyncio

    app = FastAPI()

    @app.post("/mcp")
    async def mcp() -> dict[str, bool]:
        await asyncio.sleep(1)
        return {"ok": True}

    config = DeploymentConfig(
        "hosted",
        "https://app.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        (),
    )
    middleware = HostedAuth(app, config, Provider(), lambda _method, _path: True)
    middleware.mcp_timeout = 0.01
    client = TestClient(middleware)
    assert (
        client.post(
            "https://app.example.com/mcp", headers={"authorization": "Bearer valid"}
        ).status_code
        == 504
    )


@pytest.mark.anyio
async def test_concurrent_mcp_requests_do_not_form_an_unbounded_queue() -> None:
    import httpx

    release = asyncio.Event()
    occupied = asyncio.Event()
    calls = 0
    app = FastAPI()

    @app.post("/mcp")
    async def mcp() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 4:
            occupied.set()
        await release.wait()
        return {"ok": True}

    config = DeploymentConfig(
        "hosted",
        "https://app.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        (),
    )
    boundary = HostedAuth(app, config, Provider(), lambda _method, _path: True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary),
        base_url="https://app.example.com",
        headers={"authorization": "Bearer valid"},
    ) as client:
        requests = [asyncio.create_task(client.post("/mcp", json={})) for _ in range(4)]
        try:
            await asyncio.wait_for(occupied.wait(), timeout=5)
            response = await client.post("/mcp", json={})
            assert response.status_code == 429
            assert calls == 4
        finally:
            release.set()
            await asyncio.gather(*requests)
        assert (await client.post("/mcp", json={})).status_code == 200
