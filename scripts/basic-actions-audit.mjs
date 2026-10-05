// Real browser/API/Redis/worker flow against the isolated audit server on port 8431.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.AUDIT_URL || "http://127.0.0.1:8431";
const out = path.resolve(".run/basic-actions-browser");
await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  headless: true, executablePath: process.env.CHROMIUM_PATH,
  env: { ...process.env, LANG: "C.UTF-8", LC_ALL: "C.UTF-8" },
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const errors = [];
const checks = [];
const renamed = `重命名验证-${Date.now()}`;
page.on("pageerror", e => errors.push(e.message));
function pass(name) { checks.push(name); console.log("PASS", name); }
try {
  await page.goto(base + "/videos/1");
  const actions = page.getByRole("region", { name: "视频基本操作" });
  const controls = page.locator(".player-action-row");
  const dialog = page.getByRole("dialog");
  await controls.getByRole("button", { name: "重命名", exact: true }).waitFor();
  assert.equal(await page.getByText("视频工具", { exact: true }).count(), 0);
  pass("No separate video tools entry");
  await controls.getByRole("button", { name: "重命名", exact: true }).click();
  await dialog.getByRole("textbox", { name: "新名称（不含扩展名）" }).fill(renamed);
  await dialog.getByRole("button", { name: "保存名称" }).click();
  await page.getByRole("heading", { name: renamed, exact: true }).waitFor();
  assert.ok(await actions.getByRole("status").innerText().then(t => t.includes(renamed + ".mp4")));
  const video = await page.request.get(base + "/api/videos/1").then(r => r.json());
  assert.ok(video.sources[0].local_path.endsWith("/" + renamed + ".mp4"));
  pass("UI renames the real file and refreshes title/source metadata");
  const stream = await page.request.get(base + "/api/videos/1/stream", { headers: { Range: "bytes=0-99" } });
  assert.equal(stream.status(), 206);
  pass("Renamed source still supports playback and Range requests");

  await controls.getByRole("button", { name: "转换格式", exact: true }).click();
  await dialog.getByRole("combobox", { name: "目标格式" }).selectOption("mkv");
  const submitted = page.waitForResponse(r => r.url().endsWith("/api/videos/1/convert") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "开始转换", exact: true }).click();
  const conversionJob = await (await submitted).json();
  const resultLink = actions.locator("article").filter({ hasText: "格式转换 #" + conversionJob.id }).getByRole("link", { name: "下载文件" });
  await resultLink.waitFor({ timeout: 20000 });
  const downloadPromise = page.waitForEvent("download");
  await resultLink.click();
  const download = await downloadPromise;
  assert.ok(download.suggestedFilename().endsWith(".mkv"));
  await download.saveAs(path.join(out, "converted.mkv"));
  pass("Real queue and worker produce a downloadable MKV");

  await controls.getByRole("button", { name: "重命名", exact: true }).click();
  await dialog.getByRole("checkbox", { name: "同时重命名本地文件" }).uncheck();
  await dialog.getByRole("textbox", { name: "新名称", exact: true }).fill("仅修改显示名称");
  await dialog.getByRole("button", { name: "保存名称" }).click();
  await page.getByRole("heading", { name: "仅修改显示名称" }).waitFor();
  const afterTitle = await page.request.get(base + "/api/videos/1").then(r => r.json());
  assert.equal(afterTitle.sources[0].local_path, video.sources[0].local_path);
  pass("Display-name-only option leaves the real filename unchanged");

  await controls.getByRole("button", { name: "重命名", exact: true }).click();
  await dialog.getByRole("textbox", { name: "新名称", exact: true }).fill("../bad");
  assert.ok(await dialog.getByRole("button", { name: "保存名称" }).isDisabled());
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  pass("Invalid filenames cannot be submitted");

  await controls.getByRole("button", { name: "转换格式", exact: true }).click();
  const axe = await readFile(".run/design-audit/tooling/node_modules/axe-core/axe.min.js", "utf8");
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await dialog.scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    pass("No horizontal overflow at " + width);
    await page.screenshot({ path: path.join(out, "player-" + width + ".png"), fullPage: true });
  }
  await page.addScriptTag({ content: axe });
  const accessibility = await page.evaluate(async () => (await window.axe.run(
    document.querySelector(".basic-action-dialog"),
    { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } },
  )).violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target) })));
  assert.deepEqual(accessibility, []);
  pass("Basic-action controls pass WCAG accessibility checks");

  await page.goto(base + "/jobs");
  await page.getByRole("heading", { name: "格式转换", exact: true }).first().waitFor();
  assert.ok(await page.getByRole("link", { name: "下载文件" }).count() >= 1);
  pass("Task center exposes the conversion result");
  assert.deepEqual(errors, []);
  pass("No browser runtime errors");
  await writeFile(path.join(out, "results.json"), JSON.stringify({ checks, errors, accessibility }, null, 2));
  console.log("PASS TOTAL", checks.length);
} finally {
  await browser.close();
}
