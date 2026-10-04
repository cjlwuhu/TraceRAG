import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test("versioned knowledge, document-only query and independent relevance UI", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: "知识与案例", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "每次查询，都有可追溯的来源。" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存审核并发布案例" }),
  ).toBeDisabled();
  await expect(page.getByText("BROWSER TEST FIXTURE ONLY")).toHaveCount(0);
  await mkdir("../outputs/console-qa", { recursive: true });
  await page.screenshot({
    path: "../outputs/console-qa/stage09-knowledge-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../outputs/console-qa/stage09-knowledge-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("button", { name: "诊断工作台", exact: true }).click();
  await page.locator("#event").selectOption("");
  await page.locator(".configuration > summary").click();
  await page.getByLabel("消融预设").selectOption("multisource");
  await expect(
    page.getByRole("checkbox", { name: "图片图注", exact: true }),
  ).toBeChecked();
  await page.getByLabel("消融预设").selectOption("doc_only");
  await expect(
    page.getByRole("checkbox", { name: "图片图注", exact: true }),
  ).not.toBeChecked();
  await page.locator(".configuration > summary").click();
  await page
    .getByLabel("文档范围", { exact: true })
    .selectOption("online-boutique");
  await page.locator("#question").fill("checkoutservice latency CPU 排查步骤");
  await page.getByRole("button", { name: "运行检索与工单" }).click();
  await expect(page.getByRole("heading", { name: "本次结果" })).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: "审核工单" }).click();
  await page.getByText("人工标注检索相关性", { exact: true }).click();
  await page
    .getByLabel("标注人", { exact: true })
    .fill("BROWSER_TEST_NOT_HUMAN");
  await page.locator(".relevance select").first().selectOption("2");
  await page
    .getByLabel("标注理由", { exact: true })
    .fill("自动化测试，不是研究标注。");
  await page.getByRole("button", { name: "保存相关性标注" }).click();
  await expect(
    page.getByText("已保存独立的检索相关性标注；未标注项保留未知。"),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
