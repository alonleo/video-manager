"""Cryptographic helpers and request-scoped session lookup for trusted devices."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_session
from app.models import DeviceSession, TrustedDevice

SESSION_COOKIE = "frame_device_session"
SESSION_DAYS = 30
PRIVATE_CLIENT_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def random_token(size: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_public_jwk(value: dict) -> dict:
    if value.get("kty") != "EC" or value.get("crv") != "P-256":
        raise ValueError("仅支持 P-256 设备密钥")
    x = decode_b64url(str(value.get("x", "")))
    y = decode_b64url(str(value.get("y", "")))
    if len(x) != 32 or len(y) != 32:
        raise ValueError("设备公钥格式无效")
    ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    ).public_key()
    return {"kty": "EC", "crv": "P-256", "x": value["x"], "y": value["y"]}


def verify_signature(public_jwk: dict, challenge: str, signature: str) -> bool:
    try:
        public = validate_public_jwk(public_jwk)
        raw = decode_b64url(signature)
        if len(raw) != 64:
            return False
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")
        key = ec.EllipticCurvePublicNumbers(
            int.from_bytes(decode_b64url(public["x"]), "big"),
            int.from_bytes(decode_b64url(public["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        key.verify(encode_dss_signature(r, s), decode_b64url(challenge), ec.ECDSA(hashes.SHA256()))
        return True
    except (ValueError, InvalidSignature):
        return False


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def is_private_client(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return False
    return any(address in network for network in PRIVATE_CLIENT_NETWORKS)


def is_secure(request: Request) -> bool:
    # Uvicorn only applies forwarded headers from configured trusted proxies and
    # updates the ASGI scheme for us. Reading the header directly would let a LAN
    # client spoof HTTPS.
    return request.url.scheme == "https"


@asynccontextmanager
async def request_session(request: Request):
    override = request.app.dependency_overrides.get(get_session)
    if override is None:
        async with SessionLocal() as session:
            yield session
        return
    iterator = override()
    session = await anext(iterator)
    try:
        yield session
    finally:
        await iterator.aclose()


async def active_device_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count()).select_from(TrustedDevice).where(TrustedDevice.status == "active")
    )
    return count or 0


async def lock_active_device_ids(session: AsyncSession) -> list[int]:
    """Serialize trust-set mutations and return the active device IDs."""
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
    rows = await session.scalars(
        select(TrustedDevice.id)
        .where(TrustedDevice.status == "active")
        .with_for_update()
    )
    return list(rows.all())


async def authenticated_device(session: AsyncSession, token: str | None) -> TrustedDevice | None:
    if not token:
        return None
    now = utcnow()
    row = await session.execute(
        select(DeviceSession, TrustedDevice)
        .join(TrustedDevice, TrustedDevice.id == DeviceSession.device_id)
        .where(
            DeviceSession.token_hash == token_hash(token),
            DeviceSession.revoked_at.is_(None),
            DeviceSession.expires_at > now,
            TrustedDevice.status == "active",
        )
    )
    result = row.first()
    if not result:
        return None
    device_session, device = result
    if device_session.last_seen_at < now - timedelta(minutes=5):
        device_session.last_seen_at = now
        device.last_seen_at = now
        await session.commit()
    return device


def create_device_session(session: AsyncSession, device_id: int) -> str:
    raw = random_token()
    now = utcnow()
    session.add(DeviceSession(
        token_hash=token_hash(raw),
        device_id=device_id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=SESSION_DAYS),
    ))
    return raw
