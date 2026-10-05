// Run only against .run/caption-style-audit-server.py (isolated media and database).
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.AUDIT_URL || "http://127.0.0.1:18438";
const out = ".run/caption-style-validation";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
const page = await context.newPage();
const errors = [];
context.on("page", p => p.on("pageerror", e => errors.push(e.message)));
page.on("pageerror", e => errors.push(e.message));
const pass = message => console.log("PASS", message);
const panel = () => page.getByRole("region", { name: "字幕样式设置", exact: true });
const styleOf = locator => locator.evaluate(e => {
  const c = getComputedStyle(e), span = getComputedStyle(e.querySelector("span"));
  return { color: c.color, font: c.fontFamily, size: parseFloat(c.fontSize),
    weight: c.fontWeight, align: c.textAlign, lineHeight: c.lineHeight,
    shadow: c.textShadow, background: span.backgroundColor,
    position: e.dataset.position, top: c.top, bottom: c.bottom };
});
const setRange = (root, name, value) => root.getByRole("slider", { name, exact: true }).fill(String(value));
try {
  await page.goto(base + "/settings?video=1");
  await panel().waitFor();
  await panel().getByRole("button", { name: "恢复默认样式", exact: true }).click();
  await setRange(panel(), "字幕字号", 38);
  await panel().getByRole("combobox", { name: "字幕字体", exact: true }).selectOption("serif");
  await panel().getByLabel(/^文字颜色/).fill("#ffe066");
  await panel().getByLabel(/^背景颜色/).fill("#123456");
  await setRange(panel(), "背景不透明度", 40);
  await setRange(panel(), "描边粗细", 1.5);
  await panel().getByLabel(/^描边颜色/).fill("#223344");
  await setRange(panel(), "字幕行距", 1.8);
  await panel().getByRole("combobox", { name: "显示位置", exact: true }).selectOption("top");
  await setRange(panel(), "距顶部", 12);
  await panel().getByRole("combobox", { name: "文字对齐", exact: true }).selectOption("left");
  await panel().getByRole("checkbox", { name: "粗体", exact: true }).check();
  await panel().getByRole("checkbox", { name: "文字阴影", exact: true }).uncheck();
  const preview = await styleOf(panel().locator(".wasm-captions"));
  assert.equal(preview.color, "rgb(255, 224, 102)");
  assert.equal(preview.background, "rgba(18, 52, 86, 0.4)");
  assert.equal(preview.weight, "700");
  assert.equal(preview.align, "left");
  assert.equal(preview.position, "top");
  assert.ok(preview.shadow.includes("rgb(34, 51, 68)"));
  assert.ok(preview.font.includes("Serif"));
  await panel().screenshot({ path: out + "/settings-desktop.png" });
  pass("All style controls update the actual caption preview");

  await page.reload();
  await panel().waitFor();
  assert.equal(await panel().getByRole("slider", { name: "字幕字号", exact: true }).inputValue(), "38");
  assert.equal((await styleOf(panel().locator(".wasm-captions"))).color, preview.color);
  await page.getByRole("link", { name: "返回播放", exact: true }).click();
  const caption = page.locator(".player-stage .wasm-captions");
  await caption.waitFor();
  await page.waitForFunction(() => document.querySelector(".player-stage")?.dataset.playerState === "ready");
  let actual = await styleOf(caption);
  for (const key of ["color", "font", "weight", "align", "background", "position"]) assert.equal(actual[key], preview[key]);
  pass("Saved styles survive refresh and apply to the real independent player");

  await page.getByRole("button", { name: "字幕", exact: true }).click();
  const menu = page.getByRole("region", { name: "字幕菜单", exact: true });
  await menu.getByRole("switch", { name: "原文 + 译文" }).click();
  await page.waitForFunction(() => document.querySelector(".wasm-captions")?.textContent.includes("Every frame"));
  let subtitleLoads = 0;
  page.on("request", request => { if (request.url().includes("/export?")) subtitleLoads++; });
  await menu.locator("summary").filter({ hasText: "自定义字幕样式" }).click();
  const compact = menu.locator(".caption-style-editor");
  await setRange(compact, "字幕字号", 30);
  await compact.getByLabel(/^文字颜色/).fill("#66ffcc");
  await compact.getByRole("combobox", { name: "显示位置", exact: true }).selectOption("bottom");
  await setRange(compact, "距底部", 0);
  await setRange(compact, "背景不透明度", 0);
  actual = await styleOf(caption);
  assert.equal(actual.color, "rgb(102, 255, 204)");
  assert.equal(actual.background, "rgba(18, 52, 86, 0)");
  assert.ok(parseFloat(actual.bottom) >= 82);
  assert.equal(subtitleLoads, 0, "Style changes must not refetch/regenerate subtitles");
  pass("CC controls immediately style bilingual captions and avoid the control bar");

  const second = await context.newPage();
  await second.goto(base + "/settings");
  const secondPanel = second.getByRole("region", { name: "字幕样式设置", exact: true });
  await secondPanel.getByRole("button", { name: "醒目", exact: true }).click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector(".wasm-captions")).color === "rgb(255, 224, 102)");
  await second.close();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "全屏", exact: true }).click();
  await page.waitForFunction(() => !!document.fullscreenElement);
  assert.ok(await caption.isVisible());
  assert.equal((await styleOf(caption)).color, "rgb(255, 224, 102)");
  await page.screenshot({ path: out + "/fullscreen.png" });
  await page.getByRole("button", { name: "退出全屏", exact: true }).click();
  pass("Styles sync between open tabs and remain effective in fullscreen");

  await page.getByRole("button", { name: "字幕", exact: true }).click();
  const disclosure = menu.locator("details.caption-style-disclosure");
  if (!(await disclosure.getAttribute("open")) && await disclosure.getAttribute("open") !== "") {
    await disclosure.locator("summary").click();
  }
  await compact.getByRole("button", { name: "恢复默认样式", exact: true }).click();
  assert.equal((await styleOf(caption)).color, "rgb(255, 255, 255)");
  assert.equal((await styleOf(caption)).position, "bottom");
  await menu.getByRole("switch", { name: "显示字幕", exact: false }).click();
  assert.equal(await caption.count(), 0);
  await menu.getByRole("switch", { name: "显示字幕", exact: false }).click();
  await caption.waitFor();
  pass("Reset restores defaults and subtitle visibility continues to work");

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await compact.getByRole("button", { name: "影院", exact: true }).click();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const stageBounds = await page.locator(".player-stage").boundingBox();
    const menuBounds = await menu.boundingBox();
    assert.ok(menuBounds.x >= stageBounds.x && menuBounds.x + menuBounds.width <= stageBounds.x + stageBounds.width + 1);
    await page.screenshot({ path: out + "/player-" + width + ".png" });
    await page.goto(base + "/settings?video=1");
    await panel().waitFor();
    await setRange(panel(), "字幕字号", 36);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await panel().screenshot({ path: out + "/settings-" + width + ".png" });
    await page.goto(base + "/videos/1");
    await caption.waitFor();
    await page.getByRole("button", { name: "字幕", exact: true }).click();
    await menu.locator("summary").click();
  }
  pass("Style controls, preview and CC menu work at 390px and 320px");

  await page.evaluate(() => localStorage.setItem("frame-caption-style", JSON.stringify({
    fontSize: 99999, color: "url(javascript:alert(1))", offset: -100, backgroundOpacity: 500,
    fontFamily: "invalid", position: "invalid", lineHeight: 0,
  })));
  await page.goto(base + "/settings");
  await panel().waitFor();
  assert.equal(await panel().getByRole("slider", { name: "字幕字号", exact: true }).inputValue(), "54");
  assert.equal(await panel().getByRole("slider", { name: "距底部", exact: true }).inputValue(), "0");
  assert.equal(await panel().getByLabel(/^文字颜色/).inputValue(), "#ffffff");
  await page.evaluate(() => {
    localStorage.removeItem("frame-caption-style");
    localStorage.setItem("frame-player-captions", JSON.stringify({ size: "large" }));
  });
  await page.reload();
  await panel().waitFor();
  assert.equal(await panel().getByRole("slider", { name: "字幕字号", exact: true }).inputValue(), "34");
  await page.evaluate(() => localStorage.setItem("frame-caption-style", "null"));
  await page.reload();
  await panel().waitFor();
  assert.equal(await panel().getByRole("slider", { name: "字幕字号", exact: true }).inputValue(), "26");
  assert.deepEqual(errors, []);
  pass("Old font sizes migrate; invalid saved styles are clamped or safely reset");
} finally {
  await browser.close();
}
