"""Protect every private API after the first trusted device is initialized."""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.device_auth import (
    SESSION_COOKIE,
    active_device_count,
    authenticated_device,
    is_loopback,
    request_session,
)

PUBLIC_API = {
    "/api/device-auth/status",
    "/api/device-auth/bootstrap",
    "/api/device-auth/challenge",
    "/api/device-auth/verify",
    "/api/device-auth/pair",
    "/api/device-auth/pair-status",
}
DEV_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def trusted_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin.rstrip("/") == str(request.base_url).rstrip("/"):
        return True
    # Vite changes the upstream Host header to the API target. Only accept its
    # browser origin when the request actually arrives from loopback.
    return is_loopback(request) and origin.rstrip("/") in DEV_ORIGINS


class TrustedDeviceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC_API:
            return await call_next(request)
        if path == "/api/health" and is_loopback(request):
            return await call_next(request)
        async with request_session(request) as session:
            device = await authenticated_device(session, request.cookies.get(SESSION_COOKIE))
            if device is None:
                initialized = await active_device_count(session) > 0
                if not initialized and is_loopback(request):
                    return await call_next(request)
                return JSONResponse(
                    {"detail": "此设备尚未获得访问授权"}, status_code=401
                )
            request.state.trusted_device = device
            if path.startswith("/api/trusted-devices") and not device.is_bootstrap:
                return JSONResponse(
                    {"detail": "只有初始设备可以管理信任设备"}, status_code=403
                )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not trusted_origin(request):
                return JSONResponse({"detail": "请求来源不受信任"}, status_code=403)
        return await call_next(request)
