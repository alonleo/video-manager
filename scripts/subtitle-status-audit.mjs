// Requires the isolated .run/subtitle-status-audit-server.py fixture on port 18439.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.AUDIT_URL || "http://127.0.0.1:18439";
const out = ".run/subtitle-status-validation";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [];
const batches = [];
page.on("pageerror", e => errors.push(e.message));
page.on("request", r => { if (r.url().includes("/api/subtitle-status?")) batches.push(new URL(r.url()).searchParams.getAll("video_ids")); });
const picker = () => page.getByRole("region", { name: "选择字幕所属视频", exact: true });
const row = id => picker().locator('.settings-video-row[data-video-id="' + id + '"]');
const pill = (id, kind) => row(id).locator('[data-kind="' + kind + '"] .subtitle-status-pill');
const pass = message => console.log("PASS", message);
try {
  await page.goto(base + "/settings");
  await row(1).waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".settings-video-row .subtitle-status-pill").length === 16);
  assert.equal(await pill(1, "generation").innerText(), "未生成");
  assert.equal(await pill(1, "translation").innerText(), "未翻译");
  assert.equal(await pill(2, "generation").innerText(), "已导入");
  assert.equal(await pill(3, "generation").innerText(), "已生成");
  assert.equal(await pill(4, "translation").innerText(), "已翻译");
  assert.ok((await row(4).innerText()).includes("简体中文"));
  assert.equal(await pill(5, "generation").innerText(), "生成中 42%");
  assert.equal(await pill(5, "translation").innerText(), "排队中");
  assert.equal(await pill(6, "generation").innerText(), "失败");
  assert.equal(await pill(6, "translation").innerText(), "已取消");
  assert.ok((await row(6).innerText()).includes("语音服务暂不可用"));
  assert.ok((await row(6).innerText()).includes("原字幕 1 轨"));
  assert.equal(await pill(7, "generation").innerText(), "结果已删除");
  assert.equal(await pill(8, "translation").innerText(), "翻译中 65%");
  assert.ok(batches.some(ids => ids.length === 8));
  await picker().screenshot({ path: out + "/list-desktop.png" });
  pass("Each video shows separate generation/translation states, progress, languages and retained tracks");

  assert.ok((await page.request.post(base + "/__audit_complete")).ok());
  await page.waitForFunction(() => {
    const row = document.querySelector('.settings-video-row[data-video-id="5"]');
    return row?.textContent.includes("已生成") && row?.textContent.includes("已翻译");
  }, null, { timeout: 8000 });
  assert.ok((await row(5).innerText()).includes("译文 1 轨"));
  assert.ok((await page.request.post(base + "/__audit_remove_result")).ok());
  await page.waitForFunction(() => document.querySelector('.settings-video-row[data-video-id="4"]')?.textContent.includes("结果已删除"), null, { timeout: 8000 });
  pass("Polling updates running tasks to completed and notices removed subtitle results without reloading");

  await row(6).click();
  const selected = picker().locator(".settings-selected-status");
  await selected.getByText("失败", { exact: true }).waitFor();
  assert.ok((await selected.innerText()).includes("已取消"));
  assert.ok((await selected.innerText()).includes("原字幕 1 轨"));
  await picker().getByRole("button", { name: "切换视频", exact: true }).click();
  await row(1).waitFor();
  pass("The selected video retains its statuses above the subtitle workspace");

  await picker().getByRole("button", { name: "下一页", exact: true }).click();
  await row(9).waitFor();
  await row(9).getByText("未生成", { exact: true }).waitFor();
  assert.equal(await picker().locator(".settings-video-row").count(), 2);
  await picker().getByRole("searchbox", { name: "选择视频", exact: true }).fill("正在处理");
  await picker().getByRole("button", { name: "搜索", exact: true }).click();
  await row(5).waitFor();
  await row(5).getByText("已翻译", { exact: true }).waitFor();
  assert.equal(await picker().locator(".settings-video-row").count(), 1);
  pass("Search, pagination and selection keep their own correct batch status data");

  await page.goto(base + "/settings");
  await row(1).waitFor();
  await row(1).getByText("未生成", { exact: true }).waitFor();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 950 });
    await picker().scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await picker().screenshot({ path: out + "/list-" + width + ".png" });
  }
  pass("Status rows remain readable without horizontal overflow at 390px and 320px");

  await page.route("**/api/subtitle-status?*", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"temporary offline"}' }));
  await page.reload();
  await picker().getByRole("alert").filter({ hasText: "字幕状态暂时无法更新" }).waitFor();
  assert.ok((await row(1).innerText()).includes("字幕状态读取失败"));
  await page.unroute("**/api/subtitle-status?*");
  await picker().getByRole("button", { name: "刷新状态", exact: true }).click();
  await row(1).getByText("未生成", { exact: true }).waitFor();
  assert.equal(await picker().getByRole("alert").count(), 0);
  assert.deepEqual(errors, []);
  pass("Status failures are explicit and retry restores the list; no uncaught browser errors");
} finally {
  await browser.close();
}
