"""Reproducible local correction for libmedia 1.3.1 Mp32RawFilter.
Upstream reads buffer[1]/buffer[2] after advancing i, which uses the wrong
MPEG audio header after a seek and may divide by zero when deriving timestamps.
Correct frame-relative header reads, reject invalid frame headers, and derive
timestamps from samples per frame. Refuse other versions.
"""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "frontend/public/player-engine/libmedia-1.3.1"
PATCHES = {
    "850.avplayer.js": (
        "const h=s[1]>>>3&3,l=(12&s[2])>>>2,p={dts:a",
        "const h=s[t+1]>>>3&3,l=(12&s[t+2])>>>2,p={dts:a"),
    "239.avplayer.js": (
        "const l=s[1]>>>3&3,p=(12&s[2])>>>2,u={dts:a",
        "const l=s[t+1]>>>3&3,p=(12&s[t+2])>>>2,u={dts:a"),
}
GUARDS = {
    "850.avplayer.js": [
        ("b=m.Y2(h,l);c.qg", "b=m.Y2(h,l);if(!(b>0)){t++;continue}c.qg"),
        ("let x=c.CM(this.frameHeader,b);if(p.buffer", "let x=c.CM(this.frameHeader,b);if(!Number.isFinite(x)||x<4){t++;continue}if(p.buffer"),
        ("BigInt(Math.floor(x/b*g.SF))", "BigInt(Math.floor((this.frameHeader.layer===3?384:this.frameHeader.layer===1&&h!==3?576:1152)/b*g.SF))"),
    ],
    "239.avplayer.js": [
        ("y=g.Y2(l,p);o.qg", "y=g.Y2(l,p);if(!(y>0)){t++;continue}o.qg"),
        ("let v=o.CM(this.frameHeader,y);if(u.buffer", "let v=o.CM(this.frameHeader,y);if(!Number.isFinite(v)||v<4){t++;continue}if(u.buffer"),
        ("BigInt(Math.floor(v/y*m.SF))", "BigInt(Math.floor((this.frameHeader.layer===3?384:this.frameHeader.layer===1&&l!==3?576:1152)/y*m.SF))"),
    ],
}
changed = []
for name, (before, after) in PATCHES.items():
    path = DEST / name
    source = path.read_text()
    if source.count(before) == 1:
        source = source.replace(before, after)
        path.write_text(source)
    elif source.count(after) != 1:
        raise SystemExit(f"Refusing to patch an unrecognized engine: {name}")
    for before_guard, after_guard in GUARDS[name]:
        if source.count(before_guard) == 1:
            source = source.replace(before_guard, after_guard)
        elif source.count(after_guard) != 1:
            raise SystemExit(f"Unrecognized MPEG audio guard: {name}")
    path.write_text(source)
    data = path.read_bytes()
    changed.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
# Include the modified source, copyright, and reproducible patch alongside the library.
source = next((ROOT / "frontend/node_modules/.pnpm").glob(
    "@libmedia+avformat@1.3.1*/node_modules/@libmedia/avformat/src/bsf/mp3/Mp32RawFilter.ts"))
original = source.read_text()
modified = original.replace("(buffer[1] >>> 3)", "(buffer[i + 1] >>> 3)").replace(
    "(buffer[2] & 0x0C)", "(buffer[i + 2] & 0x0C)")
modified = modified.replace("const sampleRate = mp3.getSampleRateByVersionIndex(ver, samplingFreqIndex)",
    "const sampleRate = mp3.getSampleRateByVersionIndex(ver, samplingFreqIndex)\n      if (!(sampleRate > 0)) { i++; continue }")
modified = modified.replace("let frameLength = frameHeader.getFrameLength(this.frameHeader, sampleRate)",
    "let frameLength = frameHeader.getFrameLength(this.frameHeader, sampleRate)\n      if (!Number.isFinite(frameLength) || frameLength < 4) { i++; continue }")
modified = modified.replace("frameLength / sampleRate * AV_TIME_BASE",
    "(this.frameHeader.layer === 3 ? 384 : this.frameHeader.layer === 1 && ver !== 3 ? 576 : 1152) / sampleRate * AV_TIME_BASE")
patch_dir = DEST / "patches"
patch_dir.mkdir(exist_ok=True)
(patch_dir / "Mp32RawFilter.ts").write_text(
    "// Modified by video-manage on 2026-10-05: use the current frame offset.\n" + modified)
shutil.copyfile(__file__, patch_dir / "patch-player-engine.py")
manifest = json.loads((DEST / "manifest.json").read_text())
manifest["local_patches"] = changed
(DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Patched MPEG audio header offsets in 2 decoder chunks")
