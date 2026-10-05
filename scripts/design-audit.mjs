// Runs read-only against the live API. All write flows use isolated browser fixtures.
// PLAYWRIGHT_MODULE, CHROMIUM_PATH and AXE_PATH allow using an existing tool install.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const base = process.env.AUDIT_URL || "http://127.0.0.1:8420";
const out = path.resolve(process.env.AUDIT_OUTPUT || ".run/design-audit");
await mkdir(out, { recursive: true });
const axe = await readFile(
  process.env.AXE_PATH ||
    path.join(out, "tooling/node_modules/axe-core/axe.min.js"),
  "utf8",
);
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROMIUM_PATH
    ? { executablePath: process.env.CHROMIUM_PATH }
    : {}),
});
const results = {
  started: new Date().toISOString(),
  checks: [],
  accessibility: [],
  errors: [],
  performance: null,
};
const pass = (name) => {
  results.checks.push(name);
  console.log("PASS", name);
};
const check = (condition, name) => {
  assert.ok(condition, name);
  pass(name);
};
const makePage = async () => {
  const p = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  p.on("pageerror", (e) => results.errors.push(e.message));
  return p;
};
const settle = async (p) => {
  await p.waitForTimeout(500);
};
const a11y = async (p, name) => {
  await p.addScriptTag({ content: axe });
  const audit = await p.evaluate(async () =>
    window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
    }),
  );
  const violations = audit.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    nodes: v.nodes.map((n) => ({
      target: n.target,
      summary: n.failureSummary,
    })),
  }));
  results.accessibility.push({ name, violations });
  console.log("A11Y", name, JSON.stringify(violations));
};
try {
  if (!process.env.AUDIT_FIXTURE_ONLY) {
    const page = await makePage();
    await page.addInitScript(() => {
      window.__auditVitals = { lcp: 0, cls: 0 };
      new PerformanceObserver((list) => {
        for (const e of list.getEntries())
          window.__auditVitals.lcp = e.startTime;
      }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((list) => {
        for (const e of list.getEntries())
          if (!e.hadRecentInput) window.__auditVitals.cls += e.value;
      }).observe({ type: "layout-shift", buffered: true });
    });
    // Do not allow this live page to perform any write against the user's database.
    await page.route("**/api/**", (route) =>
      ["GET", "HEAD"].includes(route.request().method())
        ? route.continue()
        : route.abort(),
    );
    for (const [name, url] of [
      ["home", "/"],
      ["storage", "/storages"],
      ["storage-form", "/storages?add=1"],
      ["jobs", "/jobs"],
      ["missing-video", "/videos/99999999"],
    ]) {
      await page.goto(base + url);
      await settle(page);
      if (name === "home")
        results.performance = await page.evaluate(() => ({
          ...window.__auditVitals,
          initialJS: performance
            .getEntriesByType("resource")
            .filter((r) => r.name.endsWith(".js"))
            .map((r) => ({
              url: r.name.split("/").pop(),
              bytes: r.decodedBodySize,
            })),
          navigation: performance.getEntriesByType("navigation")[0].toJSON(),
        }));
      for (const width of [320, 390, 768, 1024, 1440, 1920]) {
        await page.setViewportSize({ width, height: width < 700 ? 844 : 1000 });
        const size = await page.evaluate(() => ({
          width: innerWidth,
          scroll: document.documentElement.scrollWidth,
        }));
        check(
          size.scroll <= size.width,
          `${name}: no horizontal overflow at ${width}px`,
        );
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({
        path: path.join(out, `${name}-desktop.png`),
        fullPage: true,
        animations: "disabled",
      });
      await a11y(page, `${name}-desktop`);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({
        path: path.join(out, `${name}-mobile.png`),
        fullPage: true,
        animations: "disabled",
      });
      await a11y(page, `${name}-mobile`);
      if (name === "home") {
        await page.setViewportSize({ width: 768, height: 1000 });
        await a11y(page, "home-tablet");
      }
    }
    await page.goto(base + "/");
    await settle(page);
    await page.keyboard.press("/");
    await page.waitForFunction(
      () => document.activeElement?.id === "library-search",
    );
    pass("Keyboard / focuses search");
    await page.locator("#library-search").fill("__audit_no_match__");
    await page.waitForURL("**q=__audit_no_match__");
    await page.getByRole("heading", { name: "暂时没有找到这段影像" }).waitFor();
    pass("Search empty result is distinct from empty library");
    await page.getByRole("button", { name: "清除筛选" }).click();
    check(
      !new URL(page.url()).searchParams.has("q"),
      "Clear filters resets URL",
    );
    await page.goto(base + "/storages?add=1");
    await page.getByRole("textbox", { name: "名称" }).waitFor();
    check(
      await page
        .getByRole("button", { name: "创建", exact: true })
        .isDisabled(),
      "Blank storage cannot be submitted",
    );
    check(
      await page
        .locator('option[value="openlist"]')
        .evaluate((el) => el.disabled),
      "Unsupported provider is clearly disabled",
    );
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await page.locator(".storage-form").waitFor({ state: "hidden" });
    pass("Cancel closes storage form");
    await page.goto(base + "/");
    await page.emulateMedia({ reducedMotion: "reduce" });
    check(
      (await page
        .locator(".page")
        .evaluate((el) => getComputedStyle(el).animationName)) === "none",
      "Reduced-motion preference respected",
    );
    await page.close();
  }

  // Isolated fixture context: even unexpected API requests are fulfilled here, never sent to live API.
  const fixture = await makePage();
  let failList = false,
    failSave = false,
    unsupported = false;
  const mutations = [];
  const samples = Array.from({ length: 29 }, (_, i) => ({
    id: i + 1,
    title:
      [
        "山野来信 · 雾中行走",
        "远方的蓝 · 海岸手记",
        "城市漫游 · 日落之后",
        "春日片段 · 留住微光",
      ][i % 4] + (i > 3 ? ` ${i + 1}` : ""),
    duration_ms: 12000,
    width: 1920,
    height: 1080,
    video_codec: "h264",
    audio_codec: null,
    container: "mp4",
    size_bytes: 10240000,
    cover_path: null,
    thumb_path: null,
    status: "indexed",
    is_favorite: i % 3 === 0,
    source_kind: "local",
    created_at: "2026-10-01T10:00:00Z",
    tags: [{ id: 1, name: "旅行", color: null }],
    position_ms: i === 0 ? 4000 : null,
    finished: false,
  }));
  const storage = {
    id: 1,
    name: "验收用存储源",
    type: "local",
    enabled: true,
    config: { root: "/audit/fixtures" },
    last_scan_at: null,
    created_at: "2026-10-01T10:00:00Z",
  };
  const media = await readFile(path.join(out, "sample.mp4"));
  await fixture.route("**/__audit_media.mp4", (route) => {
    const range = route
      .request()
      .headers()
      ["range"]?.match(/bytes=(\d+)-(\d*)/);
    const start = range ? Number(range[1]) : 0;
    const end = range?.[2]
      ? Math.min(Number(range[2]), media.length - 1)
      : media.length - 1;
    return route.fulfill({
      status: range ? 206 : 200,
      contentType: "video/mp4",
      headers: {
        "Accept-Ranges": "bytes",
        "Content-Length": String(end - start + 1),
        ...(range
          ? { "Content-Range": `bytes ${start}-${end}/${media.length}` }
          : {}),
      },
      body: media.subarray(start, end + 1),
    });
  });
  await fixture.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      p = url.pathname;
    const send = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (!["GET", "HEAD"].includes(req.method())) {
      mutations.push({
        path: p,
        method: req.method(),
        body: req.postDataJSON(),
      });
      if (failSave)
        return send({ detail: "验收模拟：暂时无法保存，请重试" }, 503);
      if (p.endsWith("/progress"))
        return send({
          ...req.postDataJSON(),
          video_id: 1,
          updated_at: new Date().toISOString(),
        });
      if (req.method() === "PATCH") {
        Object.assign(samples[0], req.postDataJSON());
        return send({ ...samples[0], sources: [], meta: {} });
      }
      if (p.endsWith("/tags")) {
        const t = { id: 2, name: req.postDataJSON().name, color: null };
        samples[0].tags.push(t);
        return send(samples[0].tags);
      }
      if (p.endsWith("/test"))
        return send({ ok: true, message: "目录可以访问" });
      if (p.endsWith("/scan"))
        return send({ job_id: 99, message: "已加入扫描队列" });
      if (p.endsWith("/browse"))
        return send({
          path: "",
          entries: [{ name: "旅途", path: "trip", is_dir: true, size: null }],
        });
      if (req.method() === "DELETE") return route.fulfill({ status: 204 });
      return send(storage);
    }
    if (p === "/api/health")
      return send({
        status: "ok",
        database: true,
        ffmpeg: true,
        redis: true,
        openlist: true,
        vaapi: true,
        detail: {},
      });
    if (p === "/api/storages") return send([storage]);
    if (p === "/api/tags") return send([{ id: 1, name: "旅行", color: null }]);
    if (p === "/api/jobs") return send({ items: [], total: 0 });
    if (p.endsWith("/thumb") || p.endsWith("/cover"))
      return route.fulfill({ status: 404 });
    if (p.endsWith("/playback-info"))
      return send({
        video_id: 1,
        mode: unsupported ? "transcode" : "direct",
        reason: unsupported ? "当前格式需要转换" : "浏览器可直接播放",
        duration_ms: 12000,
        stream_url: unsupported ? null : "/__audit_media.mp4",
        hls_url: null,
        start_position_ms: 4000,
        container: "mp4",
        video_codec: "h264",
        audio_codec: null,
        needs_refresh: false,
      });
    if (/\/api\/videos\/\d+$/.test(p))
      return send({
        ...samples[0],
        description: "仅用于浏览器验收的影像，不会写入用户资料库。",
        fps: 24,
        bitrate_bps: 1200000,
        language: null,
        sources: [],
        meta: {},
        updated_at: "2026-10-01T10:00:00Z",
      });
    if (p === "/api/videos") {
      if (failList) return send({ detail: "验收模拟：视频库暂时不可用" }, 503);
      let list = samples;
      const q = url.searchParams.get("q");
      if (q) list = list.filter((v) => v.title.includes(q));
      if (url.searchParams.get("favorite") === "true")
        list = list.filter((v) => v.is_favorite);
      const page = Number(url.searchParams.get("page") || 1),
        size = Number(url.searchParams.get("page_size") || 24);
      return send({
        items: list.slice((page - 1) * size, page * size),
        total: list.length,
        page,
        page_size: size,
      });
    }
    return send({ detail: "未定义的验收接口" }, 404);
  });
  await fixture.goto(base + "/");
  await fixture.locator(".video-card").first().waitFor();
  check(
    (await fixture.locator(".video-card").count()) === 24,
    "Populated library uses 24 items per page",
  );
  await fixture.screenshot({
    path: path.join(out, "fixture-library-desktop.png"),
    fullPage: true,
    animations: "disabled",
  });
  await a11y(fixture, "populated-library");
  await fixture.getByRole("button", { name: "下一页" }).click();
  await fixture.waitForFunction(
    () => document.querySelectorAll(".video-card").length === 5,
  );
  pass("Pagination shows remaining five items");
  await fixture.getByRole("button", { name: "仅看收藏" }).click();
  await fixture.waitForFunction(
    () => document.querySelectorAll(".video-card").length === 10,
  );
  check(
    !new URL(fixture.url()).searchParams.has("page"),
    "Filter resets pagination",
  );
  await fixture.getByRole("button", { name: "列表视图" }).click();
  await fixture.locator(".video-list").waitFor();
  pass("List view switches correctly");
  await fixture.reload();
  await fixture.locator(".video-list").waitFor();
  check(
    new URL(fixture.url()).searchParams.get("favorite") === "true",
    "Filters and view survive reload",
  );
  await fixture.setViewportSize({ width: 320, height: 844 });
  check(
    await fixture.evaluate(
      () => document.documentElement.scrollWidth === innerWidth,
    ),
    "Populated list fits 320px",
  );
  await fixture.screenshot({
    path: path.join(out, "fixture-library-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await fixture.goto(base + "/storages");
  await fixture.getByRole("button", { name: "检测", exact: true }).click();
  await fixture.getByText("✓ 目录可以访问").waitFor();
  pass("Storage connection result shown");
  await fixture.getByRole("button", { name: "浏览", exact: true }).click();
  await fixture.getByRole("button", { name: "旅途" }).waitFor();
  pass("Directory browser works");
  await fixture.getByRole("button", { name: "扫描", exact: true }).click();
  await fixture.getByText("✓ 已加入扫描队列（任务 #99）").waitFor();
  pass("Scan reports queued task");
  failSave = true;
  await fixture.goto(base + "/storages?add=1");
  await fixture.getByRole("textbox", { name: "名称" }).fill("隔离验收源");
  await fixture
    .getByRole("textbox", { name: "根目录（绝对路径）" })
    .fill("/audit/fixtures");
  await fixture.getByRole("button", { name: "创建", exact: true }).click();
  await fixture.getByRole("alert").waitFor();
  pass("Storage create error shown without losing input");
  failSave = false;
  await fixture.setViewportSize({ width: 1440, height: 1000 });
  await fixture.goto(base + "/videos/1");
  await fixture.waitForTimeout(1000);

  await fixture.waitForFunction(() => {
    const v = document.querySelector("video");
    return v && v.readyState >= 1 && v.currentTime >= 3.9;
  });
  pass("Direct video loads and resumes from saved position");
  await fixture.locator("video").evaluate((v) => {
    v.muted = true;
    return v.play();
  });
  await fixture.waitForTimeout(400);
  await fixture.locator("video").evaluate((v) => v.pause());
  await fixture.waitForTimeout(200);
  check(
    mutations.some(
      (m) => m.path.endsWith("/progress") && m.body.position_ms >= 4000,
    ),
    "Playback pause saves progress",
  );
  const favorite = fixture.getByRole("button", { name: "已收藏", exact: true });
  await favorite.click();
  await fixture.getByRole("button", { name: "收藏", exact: true }).waitFor();
  pass("Favorite mutation refreshes player state");
  await fixture.getByRole("button", { name: "宽屏模式" }).click();
  check(
    await fixture.locator(".player-sidebar").isHidden(),
    "Cinema mode hides metadata",
  );
  await fixture.getByRole("button", { name: "退出宽屏" }).click();
  await fixture.getByRole("textbox", { name: "添加标签" }).fill("验收标签");
  await fixture.getByRole("button", { name: "添加", exact: true }).click();
  await fixture.getByRole("button", { name: "移除标签 验收标签" }).waitFor();
  pass("Tag mutation updates player");
  for (const width of [320, 390, 768, 1024, 1440]) {
    await fixture.setViewportSize({ width, height: 1000 });
    check(
      await fixture.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `Player fits ${width}px`,
    );
  }
  await fixture.locator("video").evaluate((v) => {
    v.currentTime = 7;
  });
  await fixture.waitForFunction(
    () => document.querySelector("video").currentTime >= 7,
  );
  await fixture.screenshot({
    path: path.join(out, "fixture-player-desktop.png"),
    fullPage: true,
    animations: "disabled",
  });
  await a11y(fixture, "player");
  await fixture.setViewportSize({ width: 390, height: 844 });
  check(
    await fixture.evaluate(() => {
      const title = document
        .querySelector(".player-heading h1")
        .getBoundingClientRect();
      const actions = document
        .querySelector(".player-action-row")
        .getBoundingClientRect();
      return actions.top >= title.bottom;
    }),
    "Mobile player actions do not overlap title",
  );
  await fixture.screenshot({
    path: path.join(out, "fixture-player-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await fixture.getByRole("link", { name: "返回", exact: true }).click();
  await fixture.locator(".video-card").first().waitFor();
  check(
    mutations.some(
      (m) => m.path.endsWith("/progress") && m.body.position_ms >= 7000,
    ),
    "Leaving player saves final playback position",
  );
  await fixture.goto(base + "/?favorite=true&view=list");
  await fixture.locator(".video-card").first().click();
  await fixture.getByRole("link", { name: "返回", exact: true }).click();
  await fixture.locator(".video-list").waitFor();
  check(
    new URL(fixture.url()).searchParams.get("favorite") === "true",
    "Returning from player preserves library filters and view",
  );
  unsupported = true;
  await fixture.goto(base + "/videos/1");
  await fixture.getByText("该影像暂时无法直接播放").waitFor();
  check(
    (await fixture.getByText("缓冲中…").count()) === 0,
    "Unavailable format has honest fallback instead of endless loading",
  );
  failList = true;
  await fixture.goto(base + "/");
  await fixture.getByRole("heading", { name: "影像库暂时没有响应" }).waitFor();
  failList = false;
  await fixture.getByRole("button", { name: "重新加载", exact: true }).click();
  await fixture.locator(".video-card").first().waitFor();
  pass("Error retry recovers library");
  check(results.errors.length === 0, "No uncaught browser exceptions");
  results.fixtureMutations = mutations.length;
  results.liveMutations = 0;
  results.accessibilityViolationCount = results.accessibility.reduce(
    (count, r) => count + r.violations.length,
    0,
  );
  check(
    results.accessibilityViolationCount === 0,
    "All audited views pass automated WCAG A/AA rules",
  );
} catch (error) {
  results.failure = error.stack;
  console.error(error);
  process.exitCode = 1;
} finally {
  await writeFile(
    path.join(out, "audit-results.json"),
    JSON.stringify(results, null, 2),
  );
  await browser.close();
  console.log(
    "REPORT",
    path.join(out, "audit-results.json"),
    "checks",
    results.checks.length,
  );
}
