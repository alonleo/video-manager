"""Trusted-device bootstrap, challenge authentication, pairing and revocation."""

import asyncio
import base64

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.db import get_session
from app.main import app
from app.models import Base
from app.services import device_auth


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def device_key():
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    public_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(numbers.x.to_bytes(32, "big")),
        "y": b64url(numbers.y.to_bytes(32, "big")),
        "ext": True,
        "key_ops": ["verify"],
    }
    return private, public_jwk


def sign(private, challenge: str) -> str:
    padded = challenge + "=" * (-len(challenge) % 4)
    raw = base64.urlsafe_b64decode(padded)
    der = private.sign(raw, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


@pytest_asyncio.fixture
async def auth_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/auth.db")

    @event.listens_for(engine.sync_engine, "connect")
    def fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    local = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="https://test",
    )
    remote = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.168.1.20", 1234)),
        base_url="https://test",
    )
    try:
        yield local, remote
    finally:
        await local.aclose()
        await remote.aclose()
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def bootstrap_admin(local: httpx.AsyncClient) -> None:
    _, public_jwk = device_key()
    response = await local.post(
        "/api/device-auth/bootstrap",
        json={"name": "管理设备", "public_key": public_jwk},
    )
    assert response.status_code == 201


async def request_pairing_token(local: httpx.AsyncClient) -> str:
    response = await local.post("/api/trusted-devices/pairings")
    assert response.status_code == 201
    return response.json()["token"]


async def pair_from_address(
    local: httpx.AsyncClient,
    address: str,
    *,
    scheme: str = "http",
    client_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    token = await request_pairing_token(local)
    _, public_jwk = device_key()
    body = {"token": token, "name": f"设备 {address}", "public_key": public_jwk}
    if client_type is not None:
        body["client_type"] = client_type
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(address, 1234)),
        base_url=f"{scheme}://test",
        headers=headers,
    )
    try:
        return await client.post("/api/device-auth/pair", json=body)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_android_can_pair_over_http_from_private_address(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, "192.168.1.20", client_type="android")

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_android_private_http_pairing_authentication_and_revocation(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)
    pairing = (await local.post("/api/trusted-devices/pairings")).json()
    private, public_jwk = device_key()
    android = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.168.1.70", 4321)),
        base_url="http://192.168.1.70:8420",
    )
    try:
        request_body = {
            "token": pairing["token"],
            "name": "Android 端到端设备",
            "public_key": public_jwk,
            "client_type": "android",
        }
        requested = await android.post("/api/device-auth/pair", json=request_body)
        assert requested.status_code == 202
        claim = requested.json()

        replayed = await android.post("/api/device-auth/pair", json=request_body)
        assert replayed.status_code == 409
        assert (await android.get("/api/videos")).status_code == 401

        approved = await local.post(
            f"/api/trusted-devices/pairings/{pairing['id']}/approve"
        )
        assert approved.status_code == 200
        challenge = (
            await android.post(
                "/api/device-auth/challenge", json={"device_id": claim["device_id"]}
            )
        ).json()["challenge"]
        verified = await android.post(
            "/api/device-auth/verify",
            json={
                "device_id": claim["device_id"],
                "challenge": challenge,
                "signature": sign(private, challenge),
            },
        )
        assert verified.status_code == 200
        assert "frame_device_session=" in verified.headers["set-cookie"]
        assert android.cookies.get("frame_device_session")
        assert (await android.get("/api/videos")).status_code == 200

        revoked = await local.delete(f"/api/trusted-devices/{claim['device_id']}")
        assert revoked.status_code == 204
        assert (await android.get("/api/videos")).status_code == 401
    finally:
        await android.aclose()


@pytest.mark.asyncio
async def test_browser_cannot_pair_over_http_from_private_address_by_default(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, "192.168.1.20")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_android_cannot_pair_over_http_from_public_address(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, "8.8.8.8", client_type="android")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_android_http_pairing_ignores_forwarded_public_address(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(
        local,
        "192.168.1.20",
        client_type="android",
        headers={"x-forwarded-for": "8.8.8.8"},
    )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_android_http_pairing_cannot_spoof_private_address_with_forwarded_header(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(
        local,
        "8.8.8.8",
        client_type="android",
        headers={"x-forwarded-for": "192.168.1.20"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["fd00::20", "fe80::20", "::1"])
async def test_android_can_pair_over_http_from_private_ipv6_addresses(auth_env, address):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, address, client_type="android")

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_browser_can_still_pair_over_http_from_loopback(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, "::1")

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_browser_can_pair_over_https_from_public_address(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, "8.8.8.8", scheme="https")

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_pairing_rejects_unknown_client_type(auth_env):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(
        local,
        "192.168.1.20",
        client_type="desktop",
    )

    assert response.status_code == 422


@pytest.mark.parametrize("client", [None, ("not-an-ip", 1234)])
def test_private_client_rejects_missing_or_invalid_address(client):
    request = Request({"type": "http", "client": client})

    assert device_auth.is_private_client(request) is False


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.0",
        "10.255.255.255",
        "172.16.0.0",
        "172.31.255.255",
        "192.168.0.0",
        "192.168.255.255",
        "127.0.0.0",
        "127.255.255.255",
        "fc00::",
        "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
        "fe80::",
        "febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
        "::1",
    ],
)
def test_private_client_accepts_only_supported_private_network_boundaries(address):
    request = Request({"type": "http", "client": (address, 1234)})

    assert device_auth.is_private_client(request) is True


NON_ALLOWLISTED_ADDRESSES = [
    "0.0.0.0",
    "0.1.2.3",
    "169.254.1.1",
    "192.0.2.1",
    "198.18.0.1",
    "198.51.100.1",
    "203.0.113.1",
    "224.0.0.1",
    "240.0.0.1",
    "255.255.255.255",
    "::",
    "100::1",
    "2001:db8::1",
    "::ffff:192.168.1.20",
    "::ffff:8.8.8.8",
]


@pytest.mark.parametrize("address", NON_ALLOWLISTED_ADDRESSES)
def test_private_client_rejects_addresses_outside_supported_networks(address):
    request = Request({"type": "http", "client": (address, 1234)})

    assert device_auth.is_private_client(request) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("address", NON_ALLOWLISTED_ADDRESSES)
async def test_android_http_pairing_rejects_addresses_outside_supported_networks(
    auth_env, address
):
    local, _ = auth_env
    await bootstrap_admin(local)

    response = await pair_from_address(local, address, client_type="android")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_first_device_can_only_bootstrap_from_loopback(auth_env):
    local, remote = auth_env
    _, public_jwk = device_key()
    remote_status = (await remote.get("/api/device-auth/status")).json()
    assert remote_status == {
        "initialized": False,
        "authenticated": False,
        "bootstrap_allowed": False,
        "device": None,
    }
    denied = await remote.post(
        "/api/device-auth/bootstrap", json={"name": "陌生设备", "public_key": public_jwk}
    )
    assert denied.status_code == 403

    created = await local.post(
        "/api/device-auth/bootstrap", json={"name": "服务器浏览器", "public_key": public_jwk}
    )
    assert created.status_code == 201
    assert created.json()["name"] == "服务器浏览器"
    assert (await local.get("/api/videos")).status_code == 200
    assert (await remote.get("/api/videos")).status_code == 401
    assert (await remote.get("/api/health")).status_code == 401
    assert (
        await local.post(
            "/api/trusted-devices/pairings",
            headers={"origin": "http://127.0.0.1:5173"},
        )
    ).status_code == 201
    assert (
        await local.post(
            "/api/trusted-devices/pairings",
            headers={"origin": "https://attacker.example"},
        )
    ).status_code == 403
    assert (await local.delete(f"/api/trusted-devices/{created.json()['id']}")).status_code == 409


@pytest.mark.asyncio
async def test_signature_login_rejects_replay(auth_env):
    local, remote = auth_env
    private, public_jwk = device_key()
    device = (
        await local.post(
            "/api/device-auth/bootstrap", json={"name": "主设备", "public_key": public_jwk}
        )
    ).json()

    challenge = (
        await remote.post("/api/device-auth/challenge", json={"device_id": device["id"]})
    ).json()["challenge"]
    body = {
        "device_id": device["id"],
        "challenge": challenge,
        "signature": sign(private, challenge),
    }
    assert (await remote.post("/api/device-auth/verify", json=body)).status_code == 200
    assert (await remote.get("/api/videos")).status_code == 200
    assert (await remote.post("/api/device-auth/verify", json=body)).status_code == 401
    hidden = await remote.post("/api/device-auth/challenge", json={"device_id": 999_999})
    assert hidden.status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_is_atomic(auth_env):
    local, _ = auth_env
    second = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4321)),
        base_url="https://test",
    )
    _, first_key = device_key()
    _, second_key = device_key()
    try:
        responses = await asyncio.gather(
            local.post(
                "/api/device-auth/bootstrap",
                json={"name": "首台设备 A", "public_key": first_key},
            ),
            second.post(
                "/api/device-auth/bootstrap",
                json={"name": "首台设备 B", "public_key": second_key},
            ),
        )
    finally:
        await second.aclose()
    assert sorted(response.status_code for response in responses) == [201, 409]


@pytest.mark.asyncio
async def test_pairing_decision_is_atomic(auth_env):
    local, remote = auth_env
    _, admin_public = device_key()
    await local.post(
        "/api/device-auth/bootstrap",
        json={"name": "管理设备", "public_key": admin_public},
    )
    pairing = (await local.post("/api/trusted-devices/pairings")).json()
    _, new_public = device_key()
    await remote.post(
        "/api/device-auth/pair",
        json={"token": pairing["token"], "name": "并发设备", "public_key": new_public},
    )

    decisions = await asyncio.gather(
        local.post(f"/api/trusted-devices/pairings/{pairing['id']}/approve"),
        local.post(f"/api/trusted-devices/pairings/{pairing['id']}/reject"),
    )
    assert sorted(response.status_code for response in decisions) == [200, 409]
    final = (await local.get(f"/api/trusted-devices/pairings/{pairing['id']}")).json()
    assert final["status"] in {"approved", "rejected"}


@pytest.mark.asyncio
async def test_qr_pairing_requires_existing_device_approval(auth_env):
    local, remote = auth_env
    _, admin_public = device_key()
    admin = (await local.post(
        "/api/device-auth/bootstrap", json={"name": "管理设备", "public_key": admin_public}
    )).json()
    pairing = (await local.post("/api/trusted-devices/pairings")).json()
    assert pairing["token"] and pairing["status"] == "open"

    insecure = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.168.1.21", 1234)),
        base_url="http://test",
        headers={"x-forwarded-proto": "https"},
    )
    _, insecure_public = device_key()
    try:
        insecure_claim = await insecure.post(
            "/api/device-auth/pair",
            json={"token": pairing["token"], "name": "不安全设备", "public_key": insecure_public},
        )
        assert insecure_claim.status_code == 400
    finally:
        await insecure.aclose()

    new_private, new_public = device_key()
    requested = await remote.post(
        "/api/device-auth/pair",
        json={"token": pairing["token"], "name": "客厅平板", "public_key": new_public},
    )
    assert requested.status_code == 202
    claim = requested.json()
    assert len(claim["verification_code"]) == 6
    assert (await remote.get("/api/videos")).status_code == 401
    resumable = (await local.get("/api/trusted-devices/pairings")).json()["items"]
    assert resumable[0]["id"] == pairing["id"]
    assert resumable[0]["requested_device"]["name"] == "客厅平板"
    assert (
        await remote.post(
            "/api/device-auth/pair",
            json={"token": pairing["token"], "name": "重放", "public_key": new_public},
        )
    ).status_code == 409

    pending = (await local.get(f"/api/trusted-devices/pairings/{pairing['id']}")).json()
    assert pending["verification_code"] == claim["verification_code"]
    assert pending["requested_device"]["name"] == "客厅平板"
    assert (
        await local.post(f"/api/trusted-devices/pairings/{pairing['id']}/approve")
    ).status_code == 200
    state = (
        await remote.post(
            "/api/device-auth/pair-status",
            json={"pairing_id": pairing["id"], "claim_secret": claim["claim_secret"]},
        )
    ).json()
    assert state["status"] == "approved"

    challenge = (
        await remote.post(
            "/api/device-auth/challenge", json={"device_id": claim["device_id"]}
        )
    ).json()["challenge"]
    verified = await remote.post(
        "/api/device-auth/verify",
        json={
            "device_id": claim["device_id"],
            "challenge": challenge,
            "signature": sign(new_private, challenge),
        },
    )
    assert verified.status_code == 200
    assert (await remote.get("/api/videos")).status_code == 200

    removed = await asyncio.gather(
        local.delete(f"/api/trusted-devices/{claim['device_id']}"),
        remote.delete(f"/api/trusted-devices/{admin['id']}"),
    )
    assert sum(response.status_code == 204 for response in removed) == 1
    device_lists = await asyncio.gather(
        local.get("/api/trusted-devices"),
        remote.get("/api/trusted-devices"),
    )
    authorized = [response for response in device_lists if response.status_code == 200]
    assert len(authorized) == 1
    assert len(authorized[0].json()["items"]) == 1
