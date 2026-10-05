import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ||
  "/home/leo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs");
const base = process.env.AUDIT_URL || "http://127.0.0.1:8420";
const output = process.env.AUDIT_OUTPUT || ".run/downloads-browser";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true,
  executablePath: process.env.CHROMIUM_PATH || "/home/leo/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome" });
const errors = [], checks = [], accessibility = [];
const check = (test, name) => { assert.ok(test, name); checks.push(name); console.log("PASS", name); };
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", e => errors.push(e.message));
const config = { proxy: "", cookies_browser: "", cookie_file: "", browser_profile: "", browser_keyring: "" };
let submitted, saved, canceled = false, retried = false;
let jobs = [];
const job = (id, state) => ({ id, kind: "download_url", video_id: null, state, progress: state === "done" ? 1 : .4,
  message: state === "done" ? "下载完成，已加入影像库" : "下载中 · 2.5 MB / 8 MB · 1.2 MB/s",
  payload: { url: "https://www.bilibili.com/video/BV1Wha26nEPv/" }, error: state === "error" ? "站点要求登录，请配置 Cookie" : null,
  result: state === "done" ? { title: "测试视频", filename: "测试视频.mp4", video_id: 42 } : null,
  created_at: new Date().toISOString(), started_at: null, finished_at: null });
await page.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  const respond = body => route.fulfill({ json: body });
  if (path === "/api/downloads/status") return respond({ installed: true, version: "2026.8.19", ffmpeg: true, javascript: true,
    default_directory: "/home/leo/projects/video-manage/downloads/online" });
  if (path === "/api/downloads/settings") {
    if (request.method() === "PUT") { saved = request.postDataJSON(); Object.assign(config, saved); }
    return respond(config);
  }
  if (path === "/api/downloads/cookies/check") return respond({ ok: true, total: 58, failed: 0,
    message: "读取到 58 条未过期 Cookie。", sites: { "抖音": 58, "B站": 0, "YouTube": 0 } });
  if (path === "/api/categories") return respond({ items: [{ id: "7:精选", storage_id: 7, storage_name: "本地影像",
    path: "精选", name: "精选", available: true, video_count: 0 }], warnings: [] });
  if (path === "/api/downloads" && request.method() === "POST") {
    submitted = request.postDataJSON(); jobs = [job(901, "queued")];
    return respond(jobs[0]);
  }
  if (path === "/api/downloads") return respond({ items: jobs, total: jobs.length });
  if (path === "/api/jobs/901/cancel") { canceled = true; jobs = [job(901, "canceled")]; return respond(jobs[0]); }
  if (path === "/api/downloads/901/retry") { retried = true; jobs = [job(902, "queued")]; return respond(jobs[0]); }
  if (path === "/api/jobs/notifications") return respond({ cursor: 0, pending: [], items: [], has_more: false });
  if (request.method() === "GET") return route.continue();
  return route.abort();
});
try {
  await page.goto(base + "/downloads");
  await page.getByRole("heading", { name: "一个链接，珍藏一段影像。" }).waitFor();
  await page.getByLabel("视频链接", { exact: true }).fill("复制这个链接 https://v.douyin.com/abc123/ 看视频");
  await page.getByLabel("清晰度上限").selectOption("720");
  await page.getByLabel("保存到分类").selectOption("7:精选");
  await page.getByRole("button", { name: "开始下载", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "下载任务 #901" }).waitFor();
  check(submitted.quality === "720" && submitted.storage_id === 7 && submitted.path === "精选", "share text, quality and category submitted");
  await page.getByRole("button", { name: "取消下载", exact: true }).click();
  await page.getByRole("button", { name: "重新下载", exact: true }).waitFor();
  check(canceled, "cancel action updates job");
  await page.getByRole("button", { name: "重新下载", exact: true }).click();
  await page.getByRole("button", { name: "取消下载", exact: true }).waitFor();
  check(retried, "explicit retry creates a new task");
  jobs = [job(903, "done"), job(904, "running"), job(905, "error")];
  await page.reload();
  await page.getByRole("link", { name: "播放视频" }).waitFor();
  check(await page.getByRole("link", { name: "播放视频" }).getAttribute("href") === "/videos/42", "completed task links to imported player");
  check(await page.getByRole("progressbar").count() === 1, "running task has accessible progress");
  await page.screenshot({ path: output + "/downloads-desktop.png", fullPage: true });
  const axe = await readFile(".run/design-audit/tooling/node_modules/axe-core/axe.min.js", "utf8");
  for (const [path, name] of [["/downloads", "downloads"], ["/settings/downloads", "download-settings"]]) {
    await page.goto(base + path);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.evaluate(() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))));
    await page.addScriptTag({ content: axe });
    const audit = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } }));
    accessibility.push({ name, violations: audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })) });
    await page.screenshot({ path: output + "/" + name + "-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: output + "/" + name + "-mobile.png", fullPage: true });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), name + " mobile has no horizontal overflow");
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  await page.getByLabel("下载代理（可选）").fill("http://127.0.0.1:7897");
  await page.getByLabel("浏览器登录信息").selectOption("firefox");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByRole("status").filter({ hasText: "下载设置已保存" }).waitFor();
  check(saved.cookies_browser === "firefox" && saved.proxy.endsWith(":7897") && saved.cookie_file === "", "settings save explicit browser and proxy selection");
  await page.getByLabel("浏览器登录信息").selectOption("chrome");
  await page.getByLabel("浏览器配置目录（可选）").fill("Default");
  await page.getByLabel("Linux Cookie 密钥环").selectOption("gnomekeyring");
  await page.getByRole("button", { name: "检测 Cookie", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "读取到 58 条" }).waitFor();
  check(await page.getByText("抖音：58 条", { exact: false }).count() === 1, "cookie check reports site counts");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByRole("status").filter({ hasText: "下载设置已保存" }).waitFor();
  check(saved.browser_keyring === "gnomekeyring" && saved.browser_profile === "Default", "cookie keyring and profile saved");
  await page.setViewportSize({ width: 390, height: 844 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "cookie controls fit mobile");
  await page.screenshot({ path: output + "/cookie-settings-mobile.png", fullPage: true });
  check(errors.length === 0, "no browser runtime errors");
  check(accessibility.every(x => x.violations.length === 0), "download pages have no WCAG A/AA violations");
} finally {
  await writeFile(output + "/results.json", JSON.stringify({ checks, errors, accessibility }, null, 2));
  await browser.close();
}
