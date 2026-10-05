"""Browser-cookie configuration and safe desktop-session environment discovery."""

import os
import sys
from pathlib import Path

from app.services.download_config import DownloadConfig

_DESKTOP_KEYS = {
    "XDG_CURRENT_DESKTOP",
    "DESKTOP_SESSION",
    "KDE_SESSION_VERSION",
    "KDE_FULL_SESSION",
    "GNOME_DESKTOP_SESSION_ID",
}


def browser_spec(config: DownloadConfig) -> str:
    spec = config.cookies_browser
    if config.browser_keyring and sys.platform == "linux" and spec != "firefox":
        spec += "+" + config.browser_keyring
    if config.browser_profile:
        spec += ":" + config.browser_profile
    return spec


def download_environment() -> dict[str, str]:
    env = os.environ.copy()
    if sys.platform != "linux":
        return env
    uid = os.getuid()
    runtime = Path(f"/run/user/{uid}")
    try:
        bus = runtime / "bus"
        if runtime.stat().st_uid == uid and bus.is_socket() and bus.stat().st_uid == uid:
            env.setdefault("XDG_RUNTIME_DIR", str(runtime))
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")
    except OSError:
        pass
    if not env.get("XDG_CURRENT_DESKTOP"):
        # Read only desktop hints from a same-user session, never import its secrets.
        for comm in Path("/proc").glob("[0-9]*/comm"):
            try:
                if comm.stat().st_uid != uid or comm.read_text().strip() not in {
                    "plasmashell",
                    "gnome-shell",
                    "xfce4-session",
                    "cinnamon",
                    "mate-session",
                }:
                    continue
                values = dict(
                    field.split("=", 1)
                    for field in comm.with_name("environ").read_text().split("\0")
                    if "=" in field
                )
                for key in _DESKTOP_KEYS:
                    if values.get(key):
                        env.setdefault(key, values[key])
                break
            except (OSError, UnicodeError):
                continue
    return env
