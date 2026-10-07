import { test, expect } from "@playwright/test";

test("server credential storage stays write-only through save, reload and clear", async ({
  page,
}) => {
  // Windows hosts run the real isolated backend; only its public platform label
  // is changed to exercise the Linux UI contract without mocking persistence.
  await page.route("**/api/settings", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      json: { ...body, storage: "linux_aesgcm" },
    });
  });
  let probes = 0;
  await page.route("**/api/settings/test", async (route) => {
    probes += 1;
    await route.abort();
  });
  const secrets = {
    qwen: "sk-BROWSER_SERVER_STORAGE_TEST_123456",
    glm: "BROWSER_SERVER_STORAGE_TEST_654321",
  };
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Cloud 模式").uncheck();
  await dialog.getByRole("button", { name: "模型服务", exact: true }).click();
  await expect(
    dialog.getByText("服务器加密存储", { exact: false }),
  ).toBeVisible();
  await expect(
    dialog.getByText("Windows 用户加密存储", { exact: false }),
  ).toHaveCount(0);
  await dialog.getByLabel("Qwen API Key").fill(secrets.qwen);
  await dialog.getByLabel("GLM API Key").fill(secrets.glm);
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/settings") &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "保存设置" }).click();
  expect((await saved).status()).toBe(200);
  const savedBody = await (await saved).text();
  for (const secret of Object.values(secrets))
    expect(savedBody).not.toContain(secret);
  await expect(dialog.getByRole("status")).toContainText("设置已保存");
  await expect(dialog.getByText("已加密保存", { exact: true })).toHaveCount(2);
  await expect(dialog.getByLabel("Qwen API Key")).toHaveValue("");
  await expect(dialog.getByLabel("GLM API Key")).toHaveValue("");
  await expect(
    dialog.getByRole("button", { name: "测试 GLM 连接" }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "测试 Qwen 连接" }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "关闭设置" }).click();
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await dialog.getByRole("button", { name: "模型服务", exact: true }).click();
  await expect(
    dialog.getByText("服务器加密存储", { exact: false }),
  ).toBeVisible();
  await expect(dialog.getByText("已加密保存", { exact: true })).toHaveCount(2);
  await expect(dialog.getByLabel("Qwen API Key")).toHaveValue("");
  await expect(dialog.getByLabel("GLM API Key")).toHaveValue("");
  await dialog.getByLabel("清除已配置密钥").nth(0).check();
  await dialog.getByLabel("清除已配置密钥").nth(1).check();
  await dialog.getByRole("button", { name: "保存设置" }).click();
  await expect(dialog.getByRole("status")).toContainText("设置已保存");
  await expect(dialog.getByText("已清除", { exact: true })).toHaveCount(2);
  const publicSettings = await (await page.request.get("/api/settings")).text();
  const logs = await (await page.request.get("/api/logs")).text();
  const browserStorage = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  for (const secret of Object.values(secrets)) {
    expect(publicSettings).not.toContain(secret);
    expect(logs).not.toContain(secret);
    expect(browserStorage).not.toContain(secret);
    expect(await dialog.innerText()).not.toContain(secret);
  }
  expect(probes).toBe(0);
});
