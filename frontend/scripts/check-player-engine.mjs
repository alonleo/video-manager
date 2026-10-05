import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const version = "1.3.1";
const directory = path.join(root, "public/player-engine/libmedia-" + version);
try {
  const manifest = JSON.parse(await readFile(path.join(directory, "manifest.json"), "utf8"));
  if (manifest.version !== version || manifest.assets.length < 100) throw new Error("Invalid decoder manifest");
  for (const asset of [...manifest.assets, ...(manifest.local_patches || [])]) {
    if ((await stat(path.join(directory, asset.path))).size !== asset.bytes)
      throw new Error("Decoder file size mismatch: " + asset.path);
  }
  const metadata = JSON.parse(await readFile(path.join(root, "node_modules/@libmedia/avplayer/package.json"), "utf8"));
  if (metadata.version !== version) throw new Error("Decoder version does not match the installed player");
  await stat(path.join(directory, "avplayer.js"));
  console.log("Independent player assets verified (" + version + ")");
} catch (error) {
  console.error(error.message);
  console.error("Run from the project root: python3 scripts/setup-player-engine.py");
  process.exit(1);
}
