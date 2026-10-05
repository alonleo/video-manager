"""Install pinned, same-origin libmedia modules and independently verified Wasm decoders."""
import concurrent.futures
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.3.1"
REVISION = "a5c817679b1a0f825330350121560ecbe6128c37"
DEST = ROOT / "frontend/public/player-engine" / f"libmedia-{VERSION}"
PACKAGE = ROOT / "frontend/node_modules/@libmedia/avplayer"
if json.loads((PACKAGE / "package.json").read_text())["version"] != VERSION:
    raise SystemExit("Run pnpm install with the committed lockfile first")
DEST.mkdir(parents=True, exist_ok=True)
for p in (PACKAGE / "dist/esm").glob("*.js"):
    shutil.copy2(p, DEST / p.name)

def fetch(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:
            if attempt == 3:
                print("FAILED URL", url, flush=True)
                raise

tree = json.loads(fetch(f"https://api.github.com/repos/zhaohappy/libmedia/git/trees/{REVISION}?recursive=1"))["tree"]
assets = [x for x in tree if
    x["path"].startswith(("dist/decode/", "dist/resample/", "dist/stretchpitch/"))
    and x["path"].endswith(".wasm") and not x["path"].endswith("-64.wasm")]
assets += [x for x in tree if "/" not in x["path"] and
    x["path"].startswith(("LICENSE", "COPYING"))]

def install(item):
    source = item["path"]
    relative = source.removeprefix("dist/") if source.startswith("dist/") else "licenses/" + source
    target = DEST / relative
    def verified(data):
        return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == item["sha"]
    data = target.read_bytes() if target.exists() else b""
    if not verified(data):
        data = fetch(f"https://cdn.jsdelivr.net/gh/zhaohappy/libmedia@v{VERSION}/{source}")
    if not verified(data):
        raise ValueError(f"Git blob verification failed: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(relative, len(data), flush=True)
    return {"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "git_blob": item["sha"]}

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    manifest = list(pool.map(install, assets))
(DEST / "manifest.json").write_text(json.dumps({
    "project": "https://github.com/zhaohappy/libmedia",
    "version": VERSION, "revision": REVISION, "assets": manifest,
}, indent=2) + "\n")
print(f"Installed {len(manifest)} verified assets, {sum(x['bytes'] for x in manifest)} bytes")

import subprocess
import sys
subprocess.run([sys.executable, str(ROOT / "scripts/patch-player-engine.py")], check=True)
