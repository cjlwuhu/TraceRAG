import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const qa = "../outputs/console-qa";

test("TraceRAG identity and explicit detector settings", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/TraceRAG/);
  await expect(page.locator(".brand span")).toHaveText("TraceRAG");
  await page.getByRole("button", { name: "上传 CSV" }).click();
  await expect(page.getByLabel("预热点数", { exact: true })).toHaveValue("300");
  await expect(page.getByLabel("连续确认点数", { exact: true })).toHaveValue("3");
  await expect(page.getByLabel("最少异常指标数", { exact: true })).toHaveValue("1");
  await expect(page.getByLabel("采样间隔上限（秒）", { exact: true })).toHaveValue("5");
  await page.getByLabel("连续确认点数", { exact: true }).fill("5");
  await expect(page.getByLabel("连续确认点数", { exact: true })).toHaveValue("5");
});

test("offline run, editable citations, immutable review, downloads and comparison", async ({
  page,
}) => {
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "开始一次诊断" }),
  ).toBeVisible();
  await expect(page.locator("#event")).not.toHaveValue("");
  await page.locator(".configuration > summary").click();
  await expect(page.getByRole("checkbox", { name: "云端重排" })).toBeDisabled();
  await page.locator(".configuration > summary").click();
  await mkdir(qa, { recursive: true });
  await page.screenshot({
    path: `${qa}/desktop-workspace.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "运行检索与工单" }).click();
  await expect(page.getByRole("heading", { name: "本次结果" })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.locator(".evidence-card")).toHaveCount(6);
  await page.screenshot({ path: `${qa}/desktop-results.png`, fullPage: true });
  await page.getByRole("button", { name: "审核工单" }).click();
  await expect(page.locator("#claim-0")).toBeVisible();
  await page
    .locator("#claim-0")
    .fill("浏览器自动化测试：当前仅整理证据，不确认故障原因。");
  await page.getByLabel("复核人").fill("BROWSER_TEST_NOT_HUMAN");
  await page
    .getByLabel("复核意见")
    .fill("自动化界面测试，不能作为论文人工标注。");
  await page.getByRole("button", { name: "保存人工修订" }).click();
  await expect(
    page.getByText("已保存新的人工修订版本。", { exact: false }),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "复核 JSON" }).click();
  expect((await download).suggestedFilename()).toMatch(/^rev-.*\.json$/);
  await page.screenshot({ path: `${qa}/review.png`, fullPage: true });
  await page.getByRole("button", { name: "实验记录" }).click();
  await expect(page.getByText("已完成", { exact: true })).toBeVisible();
  const picks = page.locator(".compare-select input");
  await picks.nth(0).check();
  await picks.nth(1).check();
  await page.getByRole("button", { name: "生成对照表" }).click();
  await expect(
    page.getByRole("columnheader", { name: "比较项" }),
  ).toBeVisible();
  await expect(page.getByText("尚未统一人工评测").first()).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("no-RCA preset and disabled generation are real config changes", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".configuration > summary").click();
  await page.getByLabel("消融预设").selectOption("no_rca");
  await expect(
    page.getByRole("checkbox", { name: "RCA 候选扩展查询" }),
  ).not.toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: "生成时提供 RCA 候选" }),
  ).not.toBeChecked();
  await page.getByRole("checkbox", { name: "生成草稿" }).uncheck();
  await page.getByRole("button", { name: "运行检索与工单" }).click();
  await expect(page.getByRole("heading", { name: "本次结果" })).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: "审核工单" }).click();
  await expect(
    page.getByText("本次未生成正文：generation_disabled"),
  ).toBeVisible();
});

test("real CSV bridge with RCA disabled produces an import without RCA runs", async ({
  page,
}) => {
  test.skip(!process.env.TRACERAG_TEST_CSV, "需显式提供真实 CSV 与外部 RCA 环境");
  await page.goto("/");
  await page.getByRole("button", { name: "上传 CSV" }).click();
  await page
    .getByLabel("时序 CSV", { exact: true })
    .setInputFiles(
      process.env.TRACERAG_TEST_CSV,
    );
  await page.getByRole("checkbox", { name: "RCA", exact: true }).uncheck();
  await page.getByRole("button", { name: "处理并导入" }).click();
  await expect(page.getByText("时序处理完成，事件已导入。")).toBeVisible({
    timeout: 60000,
  });
  const selected = await page.locator("#event").inputValue();
  const response = await page.request.get("/api/events/" + selected);
  expect((await response.json()).rca_runs).toEqual([]);
});

test("mobile layout, old-schema archive, and engineering warnings", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".configuration > summary")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: `${qa}/mobile-workspace.png`, fullPage: true });
  await page.getByRole("button", { name: "工单与复核" }).click();
  await page
    .locator("#order")
    .selectOption("gen-87c6bcfc46b44a338b2ba0f62a94aeed");
  await expect(page.getByText("工程复查记录")).toBeVisible();
  await page.getByText("工程复查记录").click();
  await expect(page.locator(".engineering pre")).toContainText(
    "not_fully_supported",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page
    .locator("#order")
    .selectOption("gen-29028755684a403ab86b9cb37958c07e");
  await expect(
    page.getByText("旧结构归档，不可编辑。请重新生成符合现行契约的草稿。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "保存人工修订" })).toHaveCount(
    0,
  );
});

test("settings persist without exposing credentials and missing keys appear in logs", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "设置", exact: true }),
  ).toBeVisible();
  await dialog.getByLabel("Cloud 模式").check();
  await dialog.getByLabel("默认生成服务").selectOption("glm");
  await dialog.getByRole("button", { name: "模型服务", exact: true }).click();
  await dialog.getByLabel("GLM API Key").fill("BROWSER_FAKE_GLM_SECRET_123456");
  await dialog.getByRole("button", { name: "保存设置" }).click();
  await expect(dialog.getByRole("status")).toContainText("设置已保存");
  await expect(dialog.getByLabel("GLM API Key")).toHaveValue("");
  await expect(dialog.getByText("已加密保存")).toBeVisible();
  await page.screenshot({ path: `${qa}/settings-models.png` });
  await dialog.getByRole("button", { name: "关闭设置" }).click();
  await page.reload();
  await expect(page.locator(".mode-pill")).toContainText("Cloud");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await dialog.getByRole("button", { name: "模型服务", exact: true }).click();
  await expect(dialog.getByLabel("GLM API Key")).toHaveValue("");
  await dialog.getByLabel("清除已配置密钥").first().check();
  await dialog.getByRole("button", { name: "保存设置" }).click();
  await expect(dialog.getByRole("status")).toContainText("设置已保存");
  await dialog.getByRole("button", { name: "关闭设置" }).click();
  await page.locator(".configuration > summary").click();
  await page.getByLabel("生成模式", { exact: true }).selectOption("cloud");
  await page.getByRole("button", { name: "运行检索与工单" }).click();
  await expect(page.getByRole("alert")).toContainText("GLM API Key 未配置");
  await page.getByRole("button", { name: "运行日志", exact: false }).click();
  await page.getByLabel("日志级别").selectOption("error");
  await expect(page.locator(".log-entry").first()).toContainText(
    "API Key 未配置",
  );
  await page.screenshot({ path: `${qa}/logs.png`, fullPage: true });
  const logs = await (await page.request.get("/api/logs")).text();
  expect(logs).not.toContain("BROWSER_FAKE_GLM_SECRET_123456");
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain("BROWSER_FAKE");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await dialog.getByLabel("Cloud 模式").uncheck();
  await dialog.getByLabel("默认生成服务").selectOption("qwen");
  await dialog.getByRole("button", { name: "保存设置" }).click();
  await expect(dialog.getByRole("status")).toContainText("设置已保存");
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.getByRole("button", { name: "模型服务", exact: true }).click();
  await page.screenshot({ path: `${qa}/settings-mobile.png` });
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "设置", exact: true }),
  ).toBeFocused();
});

test("connection loss is logged once and clears after recovery", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#event")).not.toHaveValue("");
  await page.route("**/api/jobs", (route) => route.abort());
  await expect(page.getByRole("alert")).toContainText("无法连接本地服务", {
    timeout: 15000,
  });
  await expect(page.locator(".mode-pill")).toContainText("未连接");
  await page.unroute("**/api/jobs");
  await expect(page.getByRole("alert")).not.toBeVisible({ timeout: 15000 });
  await expect(page.locator(".mode-pill")).toContainText("离线");
  await page.getByRole("button", { name: "运行日志", exact: false }).click();
  await expect(
    page.locator(".log-entry").filter({ hasText: "连接已恢复" }),
  ).toHaveCount(1);
});
