"""Trusted-device bootstrap, QR pairing and signature authentication."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import DeviceAuthChallenge, DevicePairing, DeviceSession, TrustedDevice
from app.services.device_auth import (
    SESSION_COOKIE,
    SESSION_DAYS,
    active_device_count,
    authenticated_device,
    create_device_session,
    is_loopback,
    is_secure,
    lock_active_device_ids,
    random_token,
    token_hash,
    utcnow,
    validate_public_jwk,
    verify_signature,
)

router = APIRouter(prefix="/api", tags=["trusted-devices"])

_challenge_requests: dict[str, deque[float]] = defaultdict(deque)
_CHALLENGE_RATE = 60


def check_challenge_rate(request: Request) -> None:
    """Bound anonymous challenge creation per directly observed client address."""
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = _challenge_requests[client]
    while attempts and attempts[0] <= now - 60:
        attempts.popleft()
    if len(attempts) >= _CHALLENGE_RATE:
        raise HTTPException(429, "认证请求过于频繁，请稍后重试")
    attempts.append(now)
    if len(_challenge_requests) > 4096:
        for key in list(_challenge_requests):
            if not _challenge_requests[key] or _challenge_requests[key][-1] <= now - 60:
                del _challenge_requests[key]


class DeviceRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    public_key: dict


class ChallengeRequest(BaseModel):
    device_id: int = Field(gt=0)


class VerifyRequest(ChallengeRequest):
    challenge: str = Field(min_length=32, max_length=128)
    signature: str = Field(min_length=32, max_length=256)


class PairRequest(DeviceRegistration):
    token: str = Field(min_length=32, max_length=128)


class PairStatusRequest(BaseModel):
    pairing_id: int = Field(gt=0)
    claim_secret: str = Field(min_length=32, max_length=128)


class RenameDeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def device_out(device: TrustedDevice, current_id: int | None = None) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "status": device.status,
        "is_bootstrap": device.is_bootstrap,
        "is_current": device.id == current_id,
        "created_at": device.created_at,
        "approved_at": device.approved_at,
        "last_seen_at": device.last_seen_at,
    }


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=is_secure(request),
        samesite="strict",
        path="/",
    )


def clean_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise HTTPException(422, "设备名称不能为空")
    return name


def public_key(value: dict) -> dict:
    try:
        return validate_public_jwk(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


async def locked_pairing(session: AsyncSession, pairing_id: int) -> DevicePairing | None:
    """Serialize all transitions from a pairing's pending/open state."""
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
    return await session.scalar(
        select(DevicePairing)
        .where(DevicePairing.id == pairing_id)
        .with_for_update()
    )


@router.get("/device-auth/status")
async def auth_status(request: Request, session: AsyncSession = Depends(get_session)):
    initialized = await active_device_count(session) > 0
    device = await authenticated_device(session, request.cookies.get(SESSION_COOKIE))
    return {
        "initialized": initialized,
        "authenticated": device is not None,
        "bootstrap_allowed": not initialized and is_loopback(request),
        "device": device_out(device, device.id) if device else None,
    }


@router.post("/device-auth/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap_device(
    body: DeviceRegistration,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    if not is_loopback(request):
        raise HTTPException(403, "首台设备只能在服务器本机初始化")
    if await lock_active_device_ids(session):
        raise HTTPException(409, "首台信任设备已经初始化")
    now = utcnow()
    device = TrustedDevice(
        name=clean_name(body.name),
        public_key=public_key(body.public_key),
        status="active",
        is_bootstrap=True,
        created_at=now,
        approved_at=now,
        last_seen_at=now,
    )
    session.add(device)
    await session.flush()
    raw_session = create_device_session(session, device.id)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "首台信任设备已经初始化") from exc
    set_session_cookie(response, request, raw_session)
    return device_out(device, device.id)


@router.post("/device-auth/challenge")
async def create_challenge(
    body: ChallengeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    check_challenge_rate(request)
    device = await session.get(TrustedDevice, body.device_id)
    now = utcnow()
    await session.execute(delete(DeviceAuthChallenge).where(DeviceAuthChallenge.expires_at <= now))
    raw = random_token()
    if device is None or device.status != "active":
        await session.commit()
        return {"challenge": raw, "expires_in": 120}
    session.add(DeviceAuthChallenge(
        challenge_hash=token_hash(raw),
        device_id=device.id,
        created_at=now,
        expires_at=now + timedelta(minutes=2),
    ))
    await session.commit()
    return {"challenge": raw, "expires_in": 120}


@router.post("/device-auth/verify")
async def verify_device(
    body: VerifyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    challenge = await session.get(DeviceAuthChallenge, token_hash(body.challenge))
    device = await session.get(TrustedDevice, body.device_id)
    valid_challenge = (
        challenge is not None
        and challenge.device_id == body.device_id
        and challenge.expires_at > utcnow()
    )
    if challenge is not None:
        await session.delete(challenge)
    if not valid_challenge or device is None or device.status != "active":
        await session.commit()
        raise HTTPException(401, "认证请求已失效")
    if not verify_signature(device.public_key, body.challenge, body.signature):
        await session.commit()
        raise HTTPException(401, "设备签名验证失败")
    raw_session = create_device_session(session, device.id)
    device.last_seen_at = utcnow()
    await session.commit()
    set_session_cookie(response, request, raw_session)
    return device_out(device, device.id)


@router.post("/device-auth/pair", status_code=status.HTTP_202_ACCEPTED)
async def request_pairing(
    body: PairRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not is_secure(request) and not is_loopback(request):
        raise HTTPException(400, "设备配对必须通过 HTTPS 完成")
    name = clean_name(body.name)
    key = public_key(body.public_key)
    pairing = await session.scalar(
        select(DevicePairing).where(DevicePairing.token_hash == token_hash(body.token))
    )
    now = utcnow()
    if pairing is None or pairing.status != "open" or pairing.expires_at <= now:
        raise HTTPException(409, "二维码已使用或已经过期")
    claimed = await session.execute(
        update(DevicePairing)
        .where(
            DevicePairing.id == pairing.id,
            DevicePairing.status == "open",
            DevicePairing.expires_at > now,
        )
        .values(status="claiming", used_at=now)
    )
    if claimed.rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "二维码已使用或已经过期")
    claim_secret = random_token()
    device = TrustedDevice(
        name=name,
        public_key=key,
        status="pending",
        is_bootstrap=False,
        created_at=now,
    )
    session.add(device)
    await session.flush()
    pairing.status = "pending"
    pairing.requested_device_id = device.id
    pairing.verification_code = f"{secrets.randbelow(1_000_000):06d}"
    pairing.claim_secret_hash = token_hash(claim_secret)
    await session.commit()
    return {
        "pairing_id": pairing.id,
        "device_id": device.id,
        "claim_secret": claim_secret,
        "verification_code": pairing.verification_code,
        "status": pairing.status,
    }


@router.post("/device-auth/pair-status")
async def pairing_status(body: PairStatusRequest, session: AsyncSession = Depends(get_session)):
    pairing = await locked_pairing(session, body.pairing_id)
    if pairing is None or pairing.claim_secret_hash != token_hash(body.claim_secret):
        raise HTTPException(404, "配对申请不存在")
    status_value = await expire_pairing(pairing, session)
    return {"status": status_value, "device_id": pairing.requested_device_id}


@router.post("/device-auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_device(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        device_session = await session.get(DeviceSession, token_hash(raw))
        if device_session:
            device_session.revoked_at = utcnow()
            await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/trusted-devices")
async def list_trusted_devices(request: Request, session: AsyncSession = Depends(get_session)):
    current_id = request.state.trusted_device.id
    devices = (
        await session.scalars(
            select(TrustedDevice)
            .where(TrustedDevice.status.in_(["active", "pending"]))
            .order_by(TrustedDevice.created_at)
        )
    ).all()
    return {"items": [device_out(device, current_id) for device in devices]}


@router.post("/trusted-devices/pairings", status_code=status.HTTP_201_CREATED)
async def create_pairing(request: Request, session: AsyncSession = Depends(get_session)):
    now = utcnow()
    raw = random_token()
    pairing = DevicePairing(
        token_hash=token_hash(raw),
        status="open",
        created_by_device_id=request.state.trusted_device.id,
        created_at=now,
        expires_at=now + timedelta(minutes=2),
    )
    session.add(pairing)
    await session.commit()
    return {
        "id": pairing.id,
        "token": raw,
        "status": pairing.status,
        "expires_at": pairing.expires_at,
    }


@router.get("/trusted-devices/pairings")
async def list_pending_pairings(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    pairings = (
        await session.scalars(
            select(DevicePairing)
            .where(
                DevicePairing.created_by_device_id == request.state.trusted_device.id,
                DevicePairing.status == "pending",
                DevicePairing.expires_at > utcnow(),
            )
            .order_by(DevicePairing.created_at.desc())
        )
    ).all()
    return {"items": [await pairing_out(pairing, session) for pairing in pairings]}


async def expire_pairing(pairing: DevicePairing, session: AsyncSession) -> str:
    if pairing.expires_at > utcnow() or pairing.status not in {"open", "pending"}:
        return pairing.status
    now = utcnow()
    pairing.status = "expired"
    pairing.decided_at = now
    if pairing.requested_device_id:
        device = await session.get(TrustedDevice, pairing.requested_device_id)
        if device and device.status == "pending":
            device.status = "revoked"
            device.revoked_at = now
    await session.commit()
    return pairing.status


async def pairing_out(pairing: DevicePairing, session: AsyncSession) -> dict:
    requested = (
        await session.get(TrustedDevice, pairing.requested_device_id)
        if pairing.requested_device_id
        else None
    )
    current_status = await expire_pairing(pairing, session)
    return {
        "id": pairing.id,
        "status": current_status,
        "verification_code": pairing.verification_code,
        "expires_at": pairing.expires_at,
        "requested_device": device_out(requested) if requested else None,
    }


@router.get("/trusted-devices/pairings/{pairing_id}")
async def get_pairing(
    pairing_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    pairing = await locked_pairing(session, pairing_id)
    if pairing is None or pairing.created_by_device_id != request.state.trusted_device.id:
        raise HTTPException(404, "配对申请不存在")
    return await pairing_out(pairing, session)


@router.post("/trusted-devices/pairings/{pairing_id}/approve")
async def approve_pairing(
    pairing_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    pairing = await locked_pairing(session, pairing_id)
    if pairing is None or pairing.created_by_device_id != request.state.trusted_device.id:
        raise HTTPException(404, "配对申请不存在")
    if (
        pairing.status != "pending"
        or pairing.expires_at <= utcnow()
        or not pairing.requested_device_id
    ):
        raise HTTPException(409, "配对申请不可批准")
    device = await session.get(TrustedDevice, pairing.requested_device_id)
    if device is None or device.status != "pending":
        raise HTTPException(409, "待批准设备不存在")
    now = utcnow()
    device.status = "active"
    device.approved_at = now
    pairing.status = "approved"
    pairing.decided_at = now
    await session.commit()
    return await pairing_out(pairing, session)


@router.post("/trusted-devices/pairings/{pairing_id}/reject")
async def reject_pairing(
    pairing_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    pairing = await locked_pairing(session, pairing_id)
    if pairing is None or pairing.created_by_device_id != request.state.trusted_device.id:
        raise HTTPException(404, "配对申请不存在")
    if pairing.status != "pending" or not pairing.requested_device_id:
        raise HTTPException(409, "配对申请不可拒绝")
    device = await session.get(TrustedDevice, pairing.requested_device_id)
    now = utcnow()
    if device:
        device.status = "revoked"
        device.revoked_at = now
    pairing.status = "rejected"
    pairing.decided_at = now
    await session.commit()
    return await pairing_out(pairing, session)


@router.patch("/trusted-devices/{device_id}")
async def rename_device(
    device_id: int,
    body: RenameDeviceRequest,
    session: AsyncSession = Depends(get_session),
):
    device = await session.get(TrustedDevice, device_id)
    if device is None or device.status != "active":
        raise HTTPException(404, "信任设备不存在")
    device.name = clean_name(body.name)
    await session.commit()
    return device_out(device)


@router.delete("/trusted-devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: int,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    active_ids = await lock_active_device_ids(session)
    device = await session.get(TrustedDevice, device_id)
    if device is None or device_id not in active_ids:
        raise HTTPException(404, "信任设备不存在")
    if len(active_ids) <= 1:
        raise HTTPException(409, "不能撤销唯一的信任设备")
    now = utcnow()
    device.status = "revoked"
    device.revoked_at = now
    await session.execute(
        update(DeviceSession)
        .where(DeviceSession.device_id == device.id, DeviceSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.commit()
    if device.id == request.state.trusted_device.id:
        response.delete_cookie(SESSION_COOKIE, path="/")
