import assert from "node:assert/strict";
import fs from "node:fs";
import { chromium } from "/home/leo/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";

const root = "/home/leo/projects/video-manage";
const out = root + "/.run/enhancement-validation";
const base = "http://127.0.0.1:18426";
const checks = [], errors = [];
const fixtures = JSON.parse(fs.readFileSync(out + "/fixtures.json"));
const check = (name, value = true) => { assert.ok(value, name); checks.push(name); console.log("PASS", name); };
const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox"] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("pageerror", e => errors.push(e.message));
const request = page.request;
const dialog = page.getByRole("dialog", { name: "画质增强", exact: true });
async function waitJob(id, timeout = 120000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const response = await request.get(base + "/api/jobs/" + id);
    const job = await response.json();
    if (job.state === "done") return job;
    if (["error","canceled"].includes(job.state)) throw new Error(JSON.stringify(job));
    await new Promise(resolve => setTimeout(resolve, 400));
  }
  throw new Error("Job timed out: " + id);
}
async function create(video, options) {
  const response = await request.post(base + "/api/videos/" + video + "/enhance", { data: options });
  assert.equal(response.status(), 202, await response.text());
  return (await response.json()).id;
}
try {
  await page.goto(base + "/videos/" + fixtures.videos[0]);
  await page.getByRole("button", { name: "画质增强", exact: true }).click();
  await dialog.waitFor();
  check("播放器工具栏可以打开画质增强弹窗");
  await dialog.getByRole("combobox", { name: /目标分辨率/ }).selectOption("1080");
  await dialog.getByRole("combobox", { name: /目标帧率/ }).selectOption("60");
  await dialog.getByRole("combobox", { name: /帧率处理/ }).selectOption("motion");
  await dialog.getByRole("combobox", { name: /输出质量/ }).selectOption("bitrate");
  await dialog.getByRole("spinbutton", { name: /目标视频码率/ }).fill("12");
  check("分辨率、60 fps 运动补帧和码率可以同时设置");
  await page.addScriptTag({ path: root + "/.run/design-audit/tooling/node_modules/axe-core/axe.min.js" });
  const violations = await page.evaluate(async () => (await axe.run("dialog[open]", { runOnly: { type:"tag",values:["wcag2a","wcag2aa","wcag21aa"] } })).violations);
  fs.writeFileSync(out + "/axe-desktop.json", JSON.stringify(violations,null,2));
  check("弹窗无 WCAG A/AA 违规", violations.length === 0);
  await page.screenshot({ path: out + "/desktop-dialog.png" });
  await dialog.getByRole("spinbutton", { name: /目标视频码率/ }).fill("0");
  check("无效码率阻止提交", await dialog.getByRole("button", { name: "开始完整增强" }).isDisabled());
  await dialog.getByRole("spinbutton", { name: /目标视频码率/ }).fill("12");
  const previewResponse = page.waitForResponse(r => r.url().endsWith("/enhance") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "生成 5 秒预览" }).click();
  const preview = await (await previewResponse).json();
  check("预览提交为独立后台任务", preview.kind === "enhance_preview");
  const previewDone = await waitJob(preview.id);
  check("实际预览输出为 1920×1080、60 fps", previewDone.result.width === 1920 && previewDone.result.height === 1080 && previewDone.result.fps === 60);
  check("预览保持 5 秒时长", Math.abs(previewDone.result.duration_ms - 5000) < 60);
  check("实际使用 Intel 硬件编码", previewDone.result.encoder === "h264_vaapi");
  await dialog.getByRole("button", { name: "从头同步播放" }).waitFor({ timeout: 15000 });
  await dialog.getByRole("button", { name: "从头同步播放" }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('dialog[open] video')].every(v => v.currentTime > .3 && !v.paused));
  check("原片与增强预览均可实际播放");
  await dialog.getByRole("button", { name: "暂停对比" }).click();
  check("对比预览可同步暂停", await dialog.locator("video").evaluateAll(videos => videos.every(v => v.paused)));
  await page.screenshot({ path: out + "/desktop-preview.png" });
  await dialog.getByRole("combobox", { name: /目标帧率/ }).selectOption("120");
  check("修改参数后提醒预览已过期", await dialog.getByText("参数已调整，下面是上一次设置的预览。").isVisible());
  await dialog.getByRole("combobox", { name: /目标分辨率/ }).selectOption("2x");
  await dialog.getByRole("combobox", { name: /帧率处理/ }).selectOption("duplicate");
  const fullResponse = page.waitForResponse(r => r.url().endsWith("/enhance") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "开始完整增强" }).click();
  const full = await (await fullResponse).json();
  await dialog.waitFor({ state: "detached" });
  check("完整任务提交后弹窗关闭");
  const done = await waitJob(full.id);
  check("实际输出 120 fps，保持原 6 秒时长", done.result.fps === 120 && Math.abs(done.result.duration_ms - 6000) < 60);
  check("2 倍放大输出正确", done.result.width === 320 && done.result.height === 180);
  await page.locator(`.task-toast[data-task-id="${full.id}"]`).waitFor({ timeout: 15000 });
  check("完成后右下角弹出增强结果通知");
  const fullCard = page.locator(".enhancement-history-item").filter({ hasText: "视频增强 #" + full.id });
  await fullCard.getByRole("link", { name: "下载增强视频" }).waitFor();
  const download = await request.get(base + await fullCard.getByRole("link", { name: "下载增强视频" }).getAttribute("href"));
  check("完整增强结果可下载", download.status() === 200 && (await download.body()).length > 1000);
  await fullCard.getByRole("button", { name: "加入影像库" }).click();
  await fullCard.getByRole("link", { name: "查看增强视频" }).waitFor({ timeout: 15000 });
  const importedHref = await fullCard.getByRole("link", { name: "查看增强视频" }).getAttribute("href");
  check("增强结果可加入影像库成为独立视频", importedHref !== "/videos/" + fixtures.videos[0]);
  const original = await (await request.get(base + "/api/videos/" + fixtures.videos[0])).json();
  check("原视频分辨率与帧率保持不变", original.width === 160 && original.height === 90 && original.fps === 24);

  await page.getByRole("button", { name: "画质增强", exact: true }).click();
  await dialog.getByRole("button", { name: /AI 超分辨率/ }).click();
  await dialog.getByRole("combobox", { name: /超分模型/ }).waitFor();
  check("AI 模型显示安装状态与大小", (await dialog.getByRole("combobox", { name: /超分模型/ }).innerText()).includes("已安装"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: out + "/mobile-dialog.png" });
  const box = await dialog.boundingBox();
  check("手机弹窗位于视口内", box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
  check("手机无横向滚动", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  const mobileViolations = await page.evaluate(async () => (await axe.run("dialog[open]", { runOnly: { type:"tag", values:["wcag2a","wcag2aa","wcag21aa"] } })).violations);
  fs.writeFileSync(out + "/axe-mobile.json",JSON.stringify(mobileViolations,null,2));
  check("手机弹窗无 WCAG A/AA 违规", mobileViolations.length === 0);
  await dialog.getByRole("button", { name: "关闭窗口" }).click();
  check("关闭弹窗后焦点回到入口", await page.getByRole("button", { name:"画质增强", exact:true }).evaluate(el => el === document.activeElement));

  const portrait = await waitJob(await create(fixtures.videos[1], { resolution:"1080", fps:60, frame_method:"motion" }));
  check("竖屏按比例输出 1080×1920", portrait.result.width === 1080 && portrait.result.height === 1920);
  const fourK = await waitJob(await create(fixtures.videos[0], { resolution:"2160", fps:60, preview:true }));
  check("4K 预览实际输出 3840×2160", fourK.result.width === 3840 && fourK.result.height === 2160);
  const ai = await waitJob(await create(fixtures.videos[2], { method:"ai",ai_model:"general",resolution:"2x" }));
  check("通用 AI 模型实际处理完整视频", ai.result.method === "ai" && ai.result.width === 128 && ai.result.height === 96);
  const anime = await waitJob(await create(fixtures.videos[2], { method:"ai",ai_model:"anime",resolution:"2x",fps:24 }));
  check("动漫 AI 模型与运动补帧可组合使用", anime.result.method === "ai" && anime.result.fps === 24);

  const canceled = await create(fixtures.videos[0], { resolution:"2160",fps:120,encoder:"software" });
  const cancelResponse = await request.post(base + "/api/jobs/" + canceled + "/cancel");
  check("处理中的增强任务可取消", cancelResponse.status() === 200 && (await cancelResponse.json()).state === "canceled");
  await new Promise(resolve => setTimeout(resolve, 1500));
  const rejectedDownload = await request.get(base + "/api/jobs/" + canceled + "/enhancement-file");
  check("取消后的任务不提供不完整文件", rejectedDownload.status() === 409);
  check("浏览器无未捕获异常", errors.length === 0);
  fs.writeFileSync(out + "/browser-results.json", JSON.stringify({ count:checks.length, checks, errors, jobs:[previewDone,done,portrait,fourK,ai,anime] },null,2));
} finally { await browser.close(); }
