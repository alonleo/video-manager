// Uses the isolated subtitle-audit-server.py fixture and local mock AI endpoints.
import assert from "node:assert/strict";
import { mkdir, writeFile, readFile } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.AUDIT_URL || "http://127.0.0.1:8423";
const out = ".run/cc-subtitle-actions-validation";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const checks = [], errors = [];
page.on("pageerror", error => errors.push(error.message));
const pass = message => { checks.push(message); console.log("PASS", message); };
const menu = () => page.getByRole("region", { name: "字幕菜单", exact: true });
const openMenu = async () => {
  const button = page.getByRole("button", { name: "字幕", exact: true });
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
};
let generatedRequests = 0, holdCompletion = false;
page.on("request", req => { if (req.url().endsWith("/subtitles/generate-saved")) generatedRequests++; });
try {
  const fixture = await (await page.request.get(base + "/api/videos/1")).json();
  assert.equal(fixture.title, "字幕工作台 · 验证影像");
  const prior = await (await page.request.get(base + "/api/videos/1/subtitles")).json();
  for (const track of prior) assert.ok((await page.request.delete(base + "/api/subtitles/" + track.id)).ok());
  await page.goto(base + "/videos/1");
  await openMenu();
  await page.waitForFunction(() => !Array.from(document.querySelectorAll(".player-subtitle-action-buttons button")).find(b => b.textContent.includes("生成字幕"))?.disabled);
  assert.equal(await menu().getByRole("button", { name: "翻译字幕", exact: true }).isDisabled(), true);
  pass("CC exposes generation with no subtitles and disables translation until a track exists");
  await menu().getByRole("link", { name: "字幕与翻译设置", exact: true }).click();
  await page.getByRole("heading", { name: "字幕与翻译", exact: true }).waitFor();
  await page.getByLabel("语音模型").fill("cc-speech-fixture");
  await page.getByLabel("视频语言").selectOption("ja");
  await page.getByLabel("翻译模型", { exact: true }).fill("cc-translation-fixture");
  await page.getByLabel("目标语言").selectOption("ja");
  await page.getByRole("button", { name: "保存字幕与翻译参数", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "字幕与翻译参数已保存" }).waitFor();
  await page.reload();
  await page.getByLabel("视频语言").selectOption("ja");
  assert.equal(await page.getByLabel("语音模型").inputValue(), "cc-speech-fixture");
  assert.equal(await page.getByLabel("目标语言").inputValue(), "ja");
  assert.equal(await page.getByLabel("翻译模型", { exact: true }).inputValue(), "cc-translation-fixture");
  const saved = await (await page.request.get(base + "/api/videos/1/subtitle-options")).json();
  assert.equal(saved.asr_model, "cc-speech-fixture");
  assert.equal(saved.target_language, "ja");
  pass("Model overrides and languages persist in settings and survive reload");
  await page.screenshot({ path: out + "/saved-settings.png", fullPage: true });
  await page.getByRole("link", { name: "返回播放", exact: true }).click();
  await openMenu();
  await menu().getByText(/生成：cc-speech-fixture/).waitFor();
  await menu().getByText(/翻译：cc-translation-fixture → 日本語/).waitFor();
  pass("CC previews the exact saved models and target language");
  // Hold only the browser's completion observation to verify reload recovery.
  await page.route(/\/api\/jobs\/\d+$/, async route => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const job = await response.json();
    await route.fulfill({ response, json: holdCompletion ? { ...job, state: "running", progress: .35, result: null, error: null } : job });
  });
  holdCompletion = true;
  await menu().getByRole("button", { name: "生成字幕", exact: true }).click();
  await page.waitForFunction(() => !!sessionStorage.getItem("frame-caption-job:1"));
  assert.equal(await menu().getByRole("button", { name: "生成字幕", exact: true }).isDisabled(), true);
  await page.keyboard.press("Escape");
  assert.equal(await menu().count(), 0);
  await page.reload();
  await openMenu();
  await menu().getByRole("progressbar", { name: "字幕任务进度" }).waitFor();
  assert.equal(await menu().getByRole("button", { name: "生成字幕", exact: true }).isDisabled(), true);
  assert.equal(generatedRequests, 1);
  pass("No duplicate submission; task persists when CC closes and across page reload");
  holdCompletion = false;
  await menu().getByRole("status").filter({ hasText: "处理完成，已切换到新字幕" }).waitFor({ timeout: 30000 });
  await menu().getByRole("button", { name: /English.*cc-speech-fixture/ }).waitFor();
  assert.equal(await menu().getByRole("button", { name: /English.*cc-speech-fixture/ }).getAttribute("aria-pressed"), "true");
  await page.waitForFunction(() => document.querySelector(".wasm-captions")?.textContent.includes("Hello from the audio fixture."));
  pass("One-click generation completes and automatically selects and displays the result");
  await menu().getByRole("button", { name: "翻译字幕", exact: true }).click();
  await menu().getByRole("button", { name: /日本語.*cc-translation-fixture/ }).waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelector(".wasm-captions")?.textContent.includes("你好"));
  assert.equal(await menu().getByRole("button", { name: /日本語.*cc-translation-fixture/ }).getAttribute("aria-pressed"), "true");
  const tracks = await (await page.request.get(base + "/api/videos/1/subtitles")).json();
  assert.ok(tracks.some(t => t.language === "ja" && t.model === "cc-translation-fixture"));
  assert.ok(tracks.some(t => t.model === "cc-speech-fixture"));
  pass("One-click translation uses saved target and model, keeps original and displays new track");
  // Action uses current server settings even if the menu preview predates another device's save.
  const updated = { ...saved, target_language: "fr", translation_model: "cc-updated-model" };
  assert.ok((await page.request.put(base + "/api/videos/1/subtitle-options", { data: updated })).ok());
  await menu().getByRole("button", { name: /English.*cc-speech-fixture/ }).click();
  await menu().getByRole("button", { name: "翻译字幕", exact: true }).click();
  await menu().getByRole("button", { name: /Français.*cc-updated-model/ }).waitFor({ timeout: 30000 });
  pass("Quick action reads latest saved parameters on the server");
  await page.route("**/api/videos/1/subtitles/generate-saved", route => route.fulfill({
    status: 409, contentType: "application/json", body: JSON.stringify({ detail: "请先到 AI 设置下载所选模型" }),
  }));
  await menu().getByRole("button", { name: "生成字幕", exact: true }).click();
  await menu().getByRole("alert").filter({ hasText: "请先到 AI 设置下载所选模型" }).waitFor();
  assert.equal(await menu().getByRole("button", { name: "生成字幕", exact: true }).isEnabled(), true);
  pass("Failed submission reports actionable error and releases the buttons");
  await page.unroute("**/api/videos/1/subtitles/generate-saved");
  await page.screenshot({ path: out + "/cc-desktop.png", fullPage: true });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "全屏", exact: true }).click();
  await page.waitForFunction(() => !!document.fullscreenElement);
  await openMenu();
  assert.equal(await menu().getByRole("button", { name: "生成字幕", exact: true }).isEnabled(), true);
  await page.screenshot({ path: out + "/cc-fullscreen.png" });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "退出全屏", exact: true }).click();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await openMenu();
    await menu().getByRole("button", { name: "生成字幕", exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await menu().getByRole("button", { name: "生成字幕", exact: true }).isVisible(), true);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: out + "/cc-mobile-" + width + ".png", fullPage: true });
    await page.keyboard.press("Escape");
  }
  pass("Quick actions remain accessible in fullscreen, 390px and 320px layouts");
  await page.setViewportSize({ width: 1440, height: 1100 });
  await openMenu();
  const axe = await readFile(".run/design-audit/tooling/node_modules/axe-core/axe.min.js", "utf8");
  await page.addScriptTag({ content: axe });
  const a11y = await page.evaluate(async () => (await window.axe.run(document.querySelector(".player-control-menu"), {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
  })).violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })));
  assert.deepEqual(errors, []);
  assert.deepEqual(a11y, []);
  await writeFile(out + "/results.json", JSON.stringify({ checks, errors, a11y }, null, 2));
  console.log(JSON.stringify({ checks: checks.length, errors, a11y }));
} catch (error) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  console.error(error);
  process.exitCode = 1;
} finally { await browser.close(); }
