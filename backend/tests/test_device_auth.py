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

from app.db import get_session
from app.main import app
from app.models import Base


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
